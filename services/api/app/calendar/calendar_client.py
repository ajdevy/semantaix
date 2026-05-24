"""Google Calendar ``freeBusy`` httpx client with bounded retry (story 11.04).

"Hand-roll the request, never the cryptography": the ``freeBusy`` query is a
plain authenticated POST over the **injected**, long-lived ``httpx.AsyncClient``
(pooling) — not ``google-api-python-client``. Failure is escalation, never a
guess: ``429`` respects ``Retry-After`` for one bounded retry; ``5xx`` /
timeout get one bounded retry; anything still unresolved becomes a
``CalendarProviderError`` (raw httpx never leaks upward) and emits an incident.

Result is a frozen ``FreeBusy`` of tz-aware busy intervals only — never event
titles (treat calendar contents as confidential).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

import httpx

logger = logging.getLogger(__name__)

_FREEBUSY_ENDPOINT = "https://www.googleapis.com/calendar/v3/freeBusy"
_MAX_RETRY_AFTER_SECONDS = 60.0
_DEFAULT_RETRY_AFTER_SECONDS = 1.0


class CalendarProviderError(Exception):
    """Raised when the calendar provider is unreachable/erroring after retry."""


@dataclass(frozen=True)
class BusyInterval:
    start: datetime
    end: datetime


@dataclass(frozen=True)
class FreeBusy:
    calendar_id: str
    busy: tuple[BusyInterval, ...] = field(default_factory=tuple)


class _Clock(Protocol):
    def now(self) -> datetime: ...


class _IncidentSink(Protocol):
    def ingest(self, *, fingerprint: str, severity: str, summary: str) -> object: ...


class CalendarFreeBusyClient:
    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient,
        clock: _Clock,
        incident_sink: _IncidentSink,
        timeout_seconds: float = 10.0,
    ) -> None:
        self._http_client = http_client
        self._clock = clock
        self._incident_sink = incident_sink
        self._timeout_seconds = timeout_seconds

    async def query_busy(
        self,
        *,
        access_token: str,
        calendar_id: str = "primary",
        time_min: datetime,
        time_max: datetime,
        trace_id: str,
    ) -> FreeBusy:
        """POST ``/freeBusy`` and return tz-aware busy intervals.

        One bounded retry for ``429`` (honouring ``Retry-After``), ``5xx``, and
        transport timeouts; then ``CalendarProviderError`` plus an incident.
        """
        body = {
            "timeMin": time_min.isoformat(),
            "timeMax": time_max.isoformat(),
            "items": [{"id": calendar_id}],
        }
        headers = {"Authorization": f"Bearer {access_token}"}

        response = await self._attempt(headers=headers, body=body, trace_id=trace_id)
        if response is None:
            response = await self._attempt(
                headers=headers, body=body, trace_id=trace_id
            )
        if response is None:
            self._emit_incident(trace_id=trace_id, reason="provider_unavailable")
            raise CalendarProviderError("provider_unavailable")

        # Safety: only 2xx may proceed to parsing. Any non-2xx that slipped past
        # the retry path (e.g. 401/403/400) must escalate — never let an empty
        # ``calendars`` dict produce a fabricated "available" answer.
        if not response.is_success:
            self._emit_incident(
                trace_id=trace_id,
                reason=f"non_success_status_{response.status_code}",
            )
            raise CalendarProviderError(
                f"non_success_status_{response.status_code}"
            )

        return self._parse(response, calendar_id=calendar_id)

    async def _attempt(
        self,
        *,
        headers: dict[str, str],
        body: dict[str, object],
        trace_id: str,
    ) -> httpx.Response | None:
        """One request. Returns the response on success, ``None`` to signal retry.

        Retryable conditions (429 / 5xx / timeout) sleep where required and
        return ``None``; a 429 that has already been retried (no retry budget
        left) is handled by the caller's single-retry contract.
        """
        try:
            response = await self._http_client.post(
                _FREEBUSY_ENDPOINT,
                json=body,
                headers=headers,
                timeout=self._timeout_seconds,
            )
        except httpx.TimeoutException:
            logger.warning("calendar_freebusy_timeout", extra={"trace_id": trace_id})
            return None

        status = response.status_code
        if status == 429:
            delay = self._retry_after_seconds(response)
            logger.warning(
                "calendar_freebusy_rate_limited",
                extra={"trace_id": trace_id, "retry_after": delay},
            )
            await asyncio.sleep(delay)
            return None
        if status >= 500:
            logger.warning(
                "calendar_freebusy_server_error",
                extra={"trace_id": trace_id, "status": status},
            )
            return None
        return response

    @staticmethod
    def _retry_after_seconds(response: httpx.Response) -> float:
        raw = response.headers.get("Retry-After")
        if raw is None:
            return _DEFAULT_RETRY_AFTER_SECONDS
        try:
            seconds = float(raw)
        except ValueError:
            return _DEFAULT_RETRY_AFTER_SECONDS
        return max(0.0, min(seconds, _MAX_RETRY_AFTER_SECONDS))

    def _emit_incident(self, *, trace_id: str, reason: str) -> None:
        self._incident_sink.ingest(
            fingerprint=f"calendar_freebusy_provider_error:{reason}",
            severity="warning",
            summary=f"Calendar freeBusy provider unavailable after retry ({reason}).",
        )
        logger.warning(
            "calendar_freebusy_provider_error",
            extra={"trace_id": trace_id, "reason": reason},
        )

    def _parse(self, response: httpx.Response, *, calendar_id: str) -> FreeBusy:
        payload = response.json()
        calendars = payload.get("calendars", {})
        entry = calendars.get(calendar_id, {})
        intervals: list[BusyInterval] = []
        for block in entry.get("busy", []):
            start = datetime.fromisoformat(block["start"])
            end = datetime.fromisoformat(block["end"])
            intervals.append(BusyInterval(start=start, end=end))
        return FreeBusy(calendar_id=calendar_id, busy=tuple(intervals))
