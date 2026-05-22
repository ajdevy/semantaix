from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from google.auth.exceptions import RefreshError

from services.api.app.calendar.oauth import (
    AccessToken,
    CalendarOAuthClient,
    OAuthExchangeError,
    OAuthTokens,
    TokenRefreshFailed,
)

_OAUTH_MODULE = "services.api.app.calendar.oauth"


def _client() -> CalendarOAuthClient:
    return CalendarOAuthClient(
        client_id="cid",
        client_secret="secret",
        redirect_uri="https://example.test/calendar/oauth/callback",
    )


def test_build_consent_url_includes_scope_state_offline_consent():
    client = _client()
    url = client.build_consent_url(state="state-123")
    assert "calendar.readonly" in url
    assert "state=state-123" in url
    assert "access_type=offline" in url
    assert "prompt=consent" in url
    assert "redirect_uri=https" in url


def test_exchange_code_maps_credentials_to_tokens(monkeypatch):
    expiry = datetime(2026, 5, 23, 12, 0, tzinfo=UTC)
    flow = Mock()
    flow.fetch_token = Mock()
    flow.credentials = SimpleNamespace(
        refresh_token="refresh-xyz", token="access-abc", expiry=expiry
    )
    monkeypatch.setattr(
        f"{_OAUTH_MODULE}.Flow.from_client_config",
        lambda config, scopes: flow,
    )
    tokens = _client().exchange_code(code="auth-code")
    assert tokens == OAuthTokens(
        refresh_token="refresh-xyz",
        access_token="access-abc",
        expiry=expiry.isoformat(),
    )
    flow.fetch_token.assert_called_once_with(code="auth-code")


def test_exchange_code_handles_missing_expiry(monkeypatch):
    flow = Mock()
    flow.fetch_token = Mock()
    flow.credentials = SimpleNamespace(
        refresh_token="refresh-xyz", token=None, expiry=None
    )
    monkeypatch.setattr(
        f"{_OAUTH_MODULE}.Flow.from_client_config",
        lambda config, scopes: flow,
    )
    tokens = _client().exchange_code(code="auth-code")
    assert tokens.refresh_token == "refresh-xyz"
    assert tokens.access_token is None
    assert tokens.expiry is None


def test_exchange_code_raises_on_fetch_failure(monkeypatch):
    flow = Mock()
    flow.fetch_token = Mock(side_effect=ValueError("boom"))
    monkeypatch.setattr(
        f"{_OAUTH_MODULE}.Flow.from_client_config",
        lambda config, scopes: flow,
    )
    with pytest.raises(OAuthExchangeError, match="exchange_failed"):
        _client().exchange_code(code="auth-code")


def test_exchange_code_raises_when_no_refresh_token(monkeypatch):
    flow = Mock()
    flow.fetch_token = Mock()
    flow.credentials = SimpleNamespace(refresh_token=None, token="access", expiry=None)
    monkeypatch.setattr(
        f"{_OAUTH_MODULE}.Flow.from_client_config",
        lambda config, scopes: flow,
    )
    with pytest.raises(OAuthExchangeError, match="no_refresh_token"):
        _client().exchange_code(code="auth-code")


def _patch_credentials(monkeypatch, *, token, expiry, refresh_exc=None):
    credentials = Mock()
    credentials.token = token
    credentials.expiry = expiry
    if refresh_exc is not None:
        credentials.refresh = Mock(side_effect=refresh_exc)
    else:
        credentials.refresh = Mock()
    monkeypatch.setattr(
        f"{_OAUTH_MODULE}.Credentials",
        lambda **kwargs: credentials,
    )
    monkeypatch.setattr(f"{_OAUTH_MODULE}.Request", lambda: Mock())
    return credentials


def test_refresh_returns_access_token_with_aware_expiry(monkeypatch):
    expiry = datetime(2026, 5, 23, 12, 0, tzinfo=UTC)
    credentials = _patch_credentials(monkeypatch, token="access-new", expiry=expiry)
    token = _client().refresh(refresh_token="refresh-xyz")
    assert token == AccessToken(access_token="access-new", expiry=expiry)
    credentials.refresh.assert_called_once()


def test_refresh_makes_naive_expiry_utc_aware(monkeypatch):
    naive_expiry = datetime(2026, 5, 23, 12, 0)
    _patch_credentials(monkeypatch, token="access-new", expiry=naive_expiry)
    token = _client().refresh(refresh_token="refresh-xyz")
    assert token.expiry == datetime(2026, 5, 23, 12, 0, tzinfo=UTC)


def test_refresh_raises_on_refresh_error(monkeypatch):
    _patch_credentials(
        monkeypatch,
        token=None,
        expiry=None,
        refresh_exc=RefreshError("invalid_grant"),
    )
    with pytest.raises(TokenRefreshFailed, match="refresh_failed"):
        _client().refresh(refresh_token="refresh-xyz")


def test_refresh_raises_when_no_token(monkeypatch):
    _patch_credentials(
        monkeypatch, token=None, expiry=datetime(2026, 5, 23, tzinfo=UTC)
    )
    with pytest.raises(TokenRefreshFailed, match="refresh_incomplete"):
        _client().refresh(refresh_token="refresh-xyz")


def test_refresh_raises_when_no_expiry(monkeypatch):
    _patch_credentials(monkeypatch, token="access-new", expiry=None)
    with pytest.raises(TokenRefreshFailed, match="refresh_incomplete"):
        _client().refresh(refresh_token="refresh-xyz")


@pytest.mark.asyncio
async def test_revoke_posts_to_google_endpoint(monkeypatch):
    response = Mock()
    response.raise_for_status = Mock()
    http_client = AsyncMock()
    http_client.post.return_value = response
    cm = AsyncMock()
    cm.__aenter__.return_value = http_client
    cm.__aexit__.return_value = None
    monkeypatch.setattr(f"{_OAUTH_MODULE}.httpx.AsyncClient", lambda timeout: cm)

    await _client().revoke(refresh_token="refresh-xyz")

    assert http_client.post.call_args.args[0].endswith("/revoke")
    assert http_client.post.call_args.kwargs["data"] == {"token": "refresh-xyz"}


@pytest.mark.asyncio
async def test_revoke_swallows_http_status_error(monkeypatch):
    response = Mock()
    response.raise_for_status = Mock(
        side_effect=httpx.HTTPStatusError(
            "bad", request=Mock(), response=Mock(status_code=400)
        )
    )
    http_client = AsyncMock()
    http_client.post.return_value = response
    cm = AsyncMock()
    cm.__aenter__.return_value = http_client
    cm.__aexit__.return_value = None
    monkeypatch.setattr(f"{_OAUTH_MODULE}.httpx.AsyncClient", lambda timeout: cm)

    await _client().revoke(refresh_token="refresh-xyz")  # must not raise


@pytest.mark.asyncio
async def test_revoke_swallows_request_error(monkeypatch):
    http_client = AsyncMock()
    http_client.post.side_effect = httpx.RequestError("down", request=Mock())
    cm = AsyncMock()
    cm.__aenter__.return_value = http_client
    cm.__aexit__.return_value = None
    monkeypatch.setattr(f"{_OAUTH_MODULE}.httpx.AsyncClient", lambda timeout: cm)

    await _client().revoke(refresh_token="refresh-xyz")  # must not raise
