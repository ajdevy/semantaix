from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from services.api.app.calendar.access_token_cache import (
    AccessTokenProvider,
    CalendarReconnectNeeded,
)
from services.api.app.calendar.oauth import AccessToken, TokenRefreshFailed
from services.api.app.calendar.token_repository import STATUS_RECONNECT_NEEDED

_NOW = datetime(2026, 5, 23, 12, 0, tzinfo=UTC)


class _FrozenClock:
    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now


class _FakeTokenRepo:
    def __init__(
        self,
        *,
        refresh_token: str = "refresh-xyz",
        initial_status: str = "connected",
    ) -> None:
        self._refresh_token = refresh_token
        self._status = initial_status
        self.status_calls: list[tuple[int, str, str]] = []
        self.deleted: list[tuple[int, str]] = []
        self.get_status_calls: list[tuple[int, str]] = []

    def get_refresh_token(self, project_id: int, operator: str) -> str:
        return self._refresh_token

    def get_status(self, project_id: int, operator: str) -> str | None:
        self.get_status_calls.append((project_id, operator))
        return self._status

    def set_status(self, project_id: int, operator: str, status: str) -> None:
        self.status_calls.append((project_id, operator, status))
        self._status = status

    def delete(self, project_id: int, operator: str) -> None:
        self.deleted.append((project_id, operator))


class _CountingOAuthClient:
    def __init__(self, *, token: AccessToken, delay: float = 0.0) -> None:
        self._token = token
        self._delay = delay
        self.refresh_count = 0

    def refresh(self, *, refresh_token: str) -> AccessToken:
        self.refresh_count += 1
        if self._delay:
            # to_thread runs this synchronously off-loop; a real sleep here lets
            # two concurrent callers contend for the single-flight lock.
            import time

            time.sleep(self._delay)
        return self._token


class _FailingOAuthClient:
    def __init__(self) -> None:
        self.refresh_count = 0

    def refresh(self, *, refresh_token: str) -> AccessToken:
        self.refresh_count += 1
        raise TokenRefreshFailed("refresh_failed")


class _FakeIncidentSink:
    def __init__(self) -> None:
        self.incidents: list[dict] = []

    def ingest(self, *, fingerprint: str, severity: str, summary: str) -> object:
        self.incidents.append(
            {"fingerprint": fingerprint, "severity": severity, "summary": summary}
        )
        return object()


class _FakeNotifier:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    async def send_message(self, *, chat_id: int, text: str) -> int:
        self.sent.append((chat_id, text))
        return 1


def _provider(
    *,
    oauth_client,
    token_repo=None,
    clock=None,
    incident_sink=None,
    notifier=None,
) -> AccessTokenProvider:
    return AccessTokenProvider(
        oauth_client=oauth_client,
        token_repo=token_repo or _FakeTokenRepo(),
        clock=clock or _FrozenClock(_NOW),
        lock_factory=asyncio.Lock,
        incident_sink=incident_sink or _FakeIncidentSink(),
        notifier=notifier or _FakeNotifier(),
    )


@pytest.mark.asyncio
async def test_returns_cached_token_when_fresh():
    token = AccessToken(access_token="abc", expiry=_NOW + timedelta(hours=1))
    oauth = _CountingOAuthClient(token=token)
    provider = _provider(oauth_client=oauth)

    first = await provider.get_access_token(
        1, "@op", operator_chat_id=99, trace_id="t1"
    )
    second = await provider.get_access_token(
        1, "@op", operator_chat_id=99, trace_id="t2"
    )

    assert first == "abc"
    assert second == "abc"
    assert oauth.refresh_count == 1  # second call served from cache


@pytest.mark.asyncio
async def test_refreshes_when_within_skew_of_expiry():
    # Token expires in 30s; default skew is 60s → considered stale, must refresh.
    stale = AccessToken(access_token="old", expiry=_NOW + timedelta(seconds=30))
    fresh = AccessToken(access_token="new", expiry=_NOW + timedelta(hours=1))
    oauth = _CountingOAuthClient(token=fresh)
    provider = _provider(oauth_client=oauth)
    provider._cache[(1, "@op")] = stale

    result = await provider.get_access_token(
        1, "@op", operator_chat_id=99, trace_id="t1"
    )

    assert result == "new"
    assert oauth.refresh_count == 1


@pytest.mark.asyncio
async def test_single_flight_two_concurrent_callers_refresh_once():
    token = AccessToken(access_token="abc", expiry=_NOW + timedelta(hours=1))
    oauth = _CountingOAuthClient(token=token, delay=0.05)
    provider = _provider(oauth_client=oauth)

    results = await asyncio.gather(
        provider.get_access_token(1, "@op", operator_chat_id=99, trace_id="t1"),
        provider.get_access_token(1, "@op", operator_chat_id=99, trace_id="t2"),
    )

    assert results == ["abc", "abc"]
    assert oauth.refresh_count == 1  # single-flight: exactly one refresh


@pytest.mark.asyncio
async def test_dead_token_first_time_sets_reconnect_emits_notifies_and_raises():
    """First dead-token detection: status flips to reconnect_needed, ONE incident,
    ONE DM to the operator, and CalendarReconnectNeeded raised. The token row is
    NOT deleted (kept with status=reconnect_needed) so that (a) subsequent
    refresh attempts can short-circuit + dedup the DM persistently across api
    restarts, and (b) the R2 connect-confirmation callback DM can recognize a
    re-consent (row present) and suppress its own message."""
    oauth = _FailingOAuthClient()
    repo = _FakeTokenRepo()  # initial_status="connected"
    sink = _FakeIncidentSink()
    notifier = _FakeNotifier()
    provider = _provider(
        oauth_client=oauth, token_repo=repo, incident_sink=sink, notifier=notifier
    )

    with pytest.raises(CalendarReconnectNeeded):
        await provider.get_access_token(
            7, "@op", operator_chat_id=42, trace_id="t1"
        )

    assert repo.status_calls == [(7, "@op", STATUS_RECONNECT_NEEDED)]
    # The row is intentionally NOT deleted — see _handle_dead_token comment.
    assert repo.deleted == []
    assert len(sink.incidents) == 1
    incident = sink.incidents[0]
    assert incident["fingerprint"] == "calendar_reconnect_needed:7:@op"
    assert "refresh-xyz" not in incident["summary"]  # token never in incident
    assert notifier.sent and notifier.sent[0][0] == 42
    assert "/connect_calendar" in notifier.sent[0][1]
    # cache cleared so a later call would re-attempt rather than serve a dead token
    assert (7, "@op") not in provider._cache


@pytest.mark.asyncio
async def test_handle_dead_token_inner_dedup_when_already_reconnect_needed():
    """Defensive belt-and-suspenders dedup inside `_handle_dead_token` itself
    (against a hypothetical race between `_refresh`'s short-circuit and the
    handler running). If `_handle_dead_token` is invoked when the row is
    already `reconnect_needed`, it MUST be a no-op: no DM, no incident, no
    status mutation. Covered by direct invocation since the short-circuit in
    `_refresh` normally prevents re-entry through the public surface."""
    oauth = _FailingOAuthClient()
    repo = _FakeTokenRepo(initial_status=STATUS_RECONNECT_NEEDED)
    sink = _FakeIncidentSink()
    notifier = _FakeNotifier()
    provider = _provider(
        oauth_client=oauth, token_repo=repo, incident_sink=sink, notifier=notifier
    )

    await provider._handle_dead_token(
        (7, "@op"), operator_chat_id=42, trace_id="t-defensive"
    )

    assert notifier.sent == []
    assert sink.incidents == []
    assert repo.status_calls == []


@pytest.mark.asyncio
async def test_dead_token_dedup_does_not_re_dm_on_repeat_calls():
    """Regression for the spam bug: once an operator has been DM'd that their
    calendar needs reconnecting, subsequent inbound calendar questions / api
    restarts MUST NOT re-DM them. The status='reconnect_needed' row acts as a
    persistent dedup flag; _refresh short-circuits and _handle_dead_token also
    no-ops if it somehow re-runs."""
    oauth = _FailingOAuthClient()
    # Already in reconnect_needed (e.g., persisted across an api restart).
    repo = _FakeTokenRepo(initial_status=STATUS_RECONNECT_NEEDED)
    sink = _FakeIncidentSink()
    notifier = _FakeNotifier()
    provider = _provider(
        oauth_client=oauth, token_repo=repo, incident_sink=sink, notifier=notifier
    )

    # Simulate three back-to-back calls from inbound customer messages.
    for trace in ("t-restart-1", "t-restart-2", "t-restart-3"):
        with pytest.raises(CalendarReconnectNeeded):
            await provider.get_access_token(
                7, "@op", operator_chat_id=42, trace_id=trace
            )

    # Google was never called (short-circuit before refresh).
    assert oauth.refresh_count == 0
    # No DMs sent at all (operator was already notified during the original
    # dead-token event).
    assert notifier.sent == []
    # No additional incidents (the original incident captured the event).
    assert sink.incidents == []
    # No status mutations (it was already reconnect_needed).
    assert repo.status_calls == []
    # The row is preserved (no delete) so a successful /connect_calendar can
    # overwrite it back to status='connected'.
    assert repo.deleted == []
