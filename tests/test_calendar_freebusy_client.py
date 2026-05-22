from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from services.api.app.calendar.calendar_client import (
    BusyInterval,
    CalendarFreeBusyClient,
    CalendarProviderError,
)

_NOW = datetime(2026, 5, 23, 12, 0, tzinfo=UTC)
_TIME_MIN = datetime(2026, 5, 23, 9, 0, tzinfo=UTC)
_TIME_MAX = datetime(2026, 5, 23, 18, 0, tzinfo=UTC)


class _FrozenClock:
    def now(self) -> datetime:
        return _NOW


class _FakeIncidentSink:
    def __init__(self) -> None:
        self.incidents: list[dict] = []

    def ingest(self, *, fingerprint: str, severity: str, summary: str) -> object:
        self.incidents.append(
            {"fingerprint": fingerprint, "severity": severity, "summary": summary}
        )
        return object()


def _ok_response(busy: list[dict], *, calendar_id: str = "primary") -> Mock:
    response = Mock()
    response.status_code = 200
    response.json.return_value = {"calendars": {calendar_id: {"busy": busy}}}
    return response


def _status_response(status: int, *, retry_after: str | None = None) -> Mock:
    response = Mock()
    response.status_code = status
    response.headers = {} if retry_after is None else {"Retry-After": retry_after}
    return response


def _client(http_client, sink=None) -> CalendarFreeBusyClient:
    return CalendarFreeBusyClient(
        http_client=http_client,
        clock=_FrozenClock(),
        incident_sink=sink or _FakeIncidentSink(),
    )


async def _query(client) -> object:
    return await client.query_busy(
        access_token="access-abc",
        time_min=_TIME_MIN,
        time_max=_TIME_MAX,
        trace_id="t1",
    )


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(
        "services.api.app.calendar.calendar_client.asyncio.sleep",
        AsyncMock(return_value=None),
    )


@pytest.mark.asyncio
async def test_parses_busy_intervals():
    http_client = AsyncMock()
    http_client.post = AsyncMock(
        return_value=_ok_response(
            [
                {
                    "start": "2026-05-23T10:00:00+00:00",
                    "end": "2026-05-23T11:00:00+00:00",
                }
            ]
        )
    )
    result = await _query(_client(http_client))
    assert result.calendar_id == "primary"
    assert result.busy == (
        BusyInterval(
            start=datetime(2026, 5, 23, 10, 0, tzinfo=UTC),
            end=datetime(2026, 5, 23, 11, 0, tzinfo=UTC),
        ),
    )


@pytest.mark.asyncio
async def test_empty_busy_when_no_blocks():
    http_client = AsyncMock()
    http_client.post = AsyncMock(return_value=_ok_response([]))
    result = await _query(_client(http_client))
    assert result.busy == ()


@pytest.mark.asyncio
async def test_429_with_retry_after_retries_once_then_raises():
    http_client = AsyncMock()
    http_client.post = AsyncMock(
        side_effect=[
            _status_response(429, retry_after="2"),
            _status_response(429, retry_after="2"),
        ]
    )
    sink = _FakeIncidentSink()
    with pytest.raises(CalendarProviderError):
        await _query(_client(http_client, sink))
    assert http_client.post.await_count == 2
    assert len(sink.incidents) == 1


@pytest.mark.asyncio
async def test_429_succeeds_after_one_retry():
    http_client = AsyncMock()
    http_client.post = AsyncMock(
        side_effect=[_status_response(429, retry_after="1"), _ok_response([])]
    )
    result = await _query(_client(http_client))
    assert result.busy == ()
    assert http_client.post.await_count == 2


@pytest.mark.asyncio
async def test_429_missing_retry_after_uses_default():
    http_client = AsyncMock()
    http_client.post = AsyncMock(
        side_effect=[_status_response(429), _ok_response([])]
    )
    result = await _query(_client(http_client))
    assert result.busy == ()


@pytest.mark.asyncio
async def test_429_invalid_retry_after_uses_default():
    http_client = AsyncMock()
    http_client.post = AsyncMock(
        side_effect=[_status_response(429, retry_after="soon"), _ok_response([])]
    )
    result = await _query(_client(http_client))
    assert result.busy == ()


@pytest.mark.asyncio
async def test_5xx_retries_once_then_raises():
    http_client = AsyncMock()
    http_client.post = AsyncMock(
        side_effect=[_status_response(503), _status_response(500)]
    )
    sink = _FakeIncidentSink()
    with pytest.raises(CalendarProviderError):
        await _query(_client(http_client, sink))
    assert http_client.post.await_count == 2
    assert len(sink.incidents) == 1


@pytest.mark.asyncio
async def test_5xx_succeeds_after_one_retry():
    http_client = AsyncMock()
    http_client.post = AsyncMock(
        side_effect=[_status_response(500), _ok_response([])]
    )
    result = await _query(_client(http_client))
    assert result.busy == ()


@pytest.mark.asyncio
async def test_timeout_retries_once_then_raises():
    http_client = AsyncMock()
    http_client.post = AsyncMock(
        side_effect=[
            httpx.ConnectTimeout("slow"),
            httpx.ReadTimeout("slow"),
        ]
    )
    sink = _FakeIncidentSink()
    with pytest.raises(CalendarProviderError):
        await _query(_client(http_client, sink))
    assert http_client.post.await_count == 2
    assert len(sink.incidents) == 1


@pytest.mark.asyncio
async def test_timeout_succeeds_after_one_retry():
    http_client = AsyncMock()
    http_client.post = AsyncMock(
        side_effect=[httpx.ReadTimeout("slow"), _ok_response([])]
    )
    result = await _query(_client(http_client))
    assert result.busy == ()
