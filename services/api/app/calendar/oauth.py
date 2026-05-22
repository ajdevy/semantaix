"""Google OAuth Authorization-Code client for the calendar-connect flow (11.02).

"Hand-roll the request, never the cryptography": the consent URL and the
code→token exchange go through ``google-auth-oauthlib``'s ``Flow`` (the focused
crypto/auth primitive); ``google-api-python-client`` is rejected. ``Flow`` is
synchronous, so ``exchange_code`` is called via ``asyncio.to_thread`` by the
endpoint. The revocation hit is a plain best-effort httpx POST.

Scope is read-only (``calendar.readonly``); offline access + ``prompt=consent``
force Google to return a refresh token on every (re)consent so the 11.01 upsert
can overwrite. Tokens / ``code`` / secrets are never logged.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx
from google_auth_oauthlib.flow import Flow

logger = logging.getLogger(__name__)

_DEFAULT_SCOPES = ("https://www.googleapis.com/auth/calendar.readonly",)
_REVOKE_ENDPOINT = "https://oauth2.googleapis.com/revoke"
_REVOKE_TIMEOUT_SECONDS = 10.0


class OAuthExchangeError(Exception):
    """Raised when Google rejects the authorization-code exchange."""


@dataclass(frozen=True)
class OAuthTokens:
    refresh_token: str
    access_token: str | None
    expiry: str | None


class CalendarOAuthClient:
    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        scopes: tuple[str, ...] = _DEFAULT_SCOPES,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._redirect_uri = redirect_uri
        self._scopes = list(scopes)

    def _build_flow(self) -> Flow:
        client_config = {
            "web": {
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [self._redirect_uri],
            }
        }
        flow = Flow.from_client_config(client_config, scopes=self._scopes)
        flow.redirect_uri = self._redirect_uri
        return flow

    def build_consent_url(self, *, state: str) -> str:
        """Read-only consent URL bound to ``state`` (offline + force consent)."""
        flow = self._build_flow()
        url, _ = flow.authorization_url(
            access_type="offline",
            prompt="consent",
            include_granted_scopes="false",
            state=state,
        )
        return url

    def exchange_code(self, *, code: str) -> OAuthTokens:
        """Sync code→token exchange (call via ``asyncio.to_thread``).

        Raises ``OAuthExchangeError`` on any transport/validation failure or
        when Google returns no refresh token (offline+consent should prevent
        the latter, but a re-consent without ``prompt=consent`` could).
        """
        flow = self._build_flow()
        try:
            flow.fetch_token(code=code)
        except Exception as exc:  # noqa: BLE001 - normalize to a typed domain error
            logger.warning("calendar_oauth_exchange_failed")
            raise OAuthExchangeError("exchange_failed") from exc
        credentials = flow.credentials
        refresh_token = getattr(credentials, "refresh_token", None)
        if not refresh_token:
            logger.warning("calendar_oauth_no_refresh_token")
            raise OAuthExchangeError("no_refresh_token")
        expiry = getattr(credentials, "expiry", None)
        return OAuthTokens(
            refresh_token=refresh_token,
            access_token=getattr(credentials, "token", None),
            expiry=expiry.isoformat() if expiry is not None else None,
        )

    async def revoke(self, *, refresh_token: str) -> None:
        """Best-effort revocation; swallow+log failure (caller still deletes)."""
        try:
            async with httpx.AsyncClient(timeout=_REVOKE_TIMEOUT_SECONDS) as client:
                response = await client.post(
                    _REVOKE_ENDPOINT,
                    data={"token": refresh_token},
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
                response.raise_for_status()
        except (httpx.HTTPStatusError, httpx.RequestError):
            logger.warning("calendar_oauth_revoke_failed")
