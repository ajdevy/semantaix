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
    def __init__(self, *, refresh_token: str = "refresh-xyz") -> None:
        self._refresh_token = refresh_token
        self.status_calls: list[tuple[int, str, str]] = []
        self.deleted: list[tuple[int, str]] = []

    def get_refresh_token(self, project_id: int, operator: str) -> str:
        return self._refresh_token

    def set_status(self, project_id: int, operator: str, status: str) -> None:
        self.status_calls.append((project_id, operator, status))

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
async def test_dead_token_sets_reconnect_deletes_emits_notifies_and_raises():
    oauth = _FailingOAuthClient()
    repo = _FakeTokenRepo()
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
    assert repo.deleted == [(7, "@op")]
    assert len(sink.incidents) == 1
    incident = sink.incidents[0]
    assert incident["fingerprint"] == "calendar_reconnect_needed:7:@op"
    assert "refresh-xyz" not in incident["summary"]  # token never in incident
    assert notifier.sent and notifier.sent[0][0] == 42
    assert "/connect_calendar" in notifier.sent[0][1]
    # cache cleared so a later call would re-attempt rather than serve a dead token
    assert (7, "@op") not in provider._cache
