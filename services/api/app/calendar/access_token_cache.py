"""Access-token minting/caching with single-flight refresh (story 11.04).

The cache holds a per-``(project_id, operator)`` short-lived access token and
refreshes it only when it is missing or within ``skew`` of expiry — never per
request (performance rule). Refresh is **single-flight**: a per-key
``asyncio.Lock`` (from an injected factory) serialises concurrent inbound
messages so they neither double-mint nor race the SQLite write.

When the refresh token itself is dead (``TokenRefreshFailed``) the cache moves
the operator to ``reconnect_needed``, deletes the poison row, emits an incident,
notifies the operator over Telegram to re-run ``/connect_calendar``, and raises
``CalendarReconnectNeeded`` to the caller (11.07 translates it to an
escalation). Tokens never reach a log line or an incident summary.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Protocol

from services.api.app.calendar.oauth import AccessToken, TokenRefreshFailed
from services.api.app.calendar.token_repository import STATUS_RECONNECT_NEEDED

logger = logging.getLogger(__name__)

_DEFAULT_SKEW = timedelta(seconds=60)
_RECONNECT_MESSAGE = (
    "Доступ к календарю больше недействителен. "
    "Пожалуйста, переподключите его командой /connect_calendar."
)


class CalendarReconnectNeeded(Exception):
    """Raised when the operator's refresh token is dead and re-consent is required."""


class _OAuthClient(Protocol):
    def refresh(self, *, refresh_token: str) -> AccessToken: ...


class _TokenRepo(Protocol):
    def get_refresh_token(self, project_id: int, operator: str) -> str: ...
    def set_status(self, project_id: int, operator: str, status: str) -> None: ...
    def delete(self, project_id: int, operator: str) -> None: ...


class _Clock(Protocol):
    def now(self) -> datetime: ...


class _IncidentSink(Protocol):
    def ingest(self, *, fingerprint: str, severity: str, summary: str) -> object: ...


class _Notifier(Protocol):
    async def send_message(self, *, chat_id: int, text: str) -> int: ...


class AccessTokenProvider:
    def __init__(
        self,
        *,
        oauth_client: _OAuthClient,
        token_repo: _TokenRepo,
        clock: _Clock,
        lock_factory,
        incident_sink: _IncidentSink,
        notifier: _Notifier,
        skew: timedelta = _DEFAULT_SKEW,
    ) -> None:
        self._oauth_client = oauth_client
        self._token_repo = token_repo
        self._clock = clock
        self._lock_factory = lock_factory
        self._incident_sink = incident_sink
        self._notifier = notifier
        self._skew = skew
        self._cache: dict[tuple[int, str], AccessToken] = {}
        self._locks: dict[tuple[int, str], asyncio.Lock] = {}

    def _lock_for(self, key: tuple[int, str]) -> asyncio.Lock:
        lock = self._locks.get(key)
        if lock is None:
            lock = self._lock_factory()
            self._locks[key] = lock
        return lock

    def _is_fresh(self, token: AccessToken) -> bool:
        return self._clock.now() < token.expiry - self._skew

    async def get_access_token(
        self,
        project_id: int,
        operator: str,
        *,
        operator_chat_id: int,
        trace_id: str,
    ) -> str:
        """Return a valid access token, refreshing under a single-flight lock.

        ``operator_chat_id``/``trace_id`` ride along for the reconnect DM and the
        incident — they are never logged as secrets.
        """
        key = (project_id, operator)
        cached = self._cache.get(key)
        if cached is not None and self._is_fresh(cached):
            return cached.access_token

        async with self._lock_for(key):
            # Re-check inside the lock: a sibling caller may have just refreshed.
            cached = self._cache.get(key)
            if cached is not None and self._is_fresh(cached):
                return cached.access_token
            return await self._refresh(
                key, operator_chat_id=operator_chat_id, trace_id=trace_id
            )

    async def _refresh(
        self,
        key: tuple[int, str],
        *,
        operator_chat_id: int,
        trace_id: str,
    ) -> str:
        project_id, operator = key
        refresh_token = await asyncio.to_thread(
            self._token_repo.get_refresh_token, project_id, operator
        )
        try:
            token = await asyncio.to_thread(
                self._oauth_client.refresh, refresh_token=refresh_token
            )
        except TokenRefreshFailed as exc:
            await self._handle_dead_token(
                key, operator_chat_id=operator_chat_id, trace_id=trace_id
            )
            raise CalendarReconnectNeeded("reconnect_needed") from exc
        self._cache[key] = token
        logger.info("calendar_access_token_refreshed", extra={"trace_id": trace_id})
        return token.access_token

    async def _handle_dead_token(
        self,
        key: tuple[int, str],
        *,
        operator_chat_id: int,
        trace_id: str,
    ) -> None:
        project_id, operator = key
        self._cache.pop(key, None)
        await asyncio.to_thread(
            self._token_repo.set_status, project_id, operator, STATUS_RECONNECT_NEEDED
        )
        await asyncio.to_thread(self._token_repo.delete, project_id, operator)
        await asyncio.to_thread(
            self._incident_sink.ingest,
            fingerprint=f"calendar_reconnect_needed:{project_id}:{operator}",
            severity="warning",
            summary=(
                f"Calendar refresh token revoked/expired for operator {operator} "
                f"(project {project_id}); reconnect required."
            ),
        )
        logger.warning(
            "calendar_token_reconnect_needed", extra={"trace_id": trace_id}
        )
        await self._notifier.send_message(
            chat_id=operator_chat_id, text=_RECONNECT_MESSAGE
        )
