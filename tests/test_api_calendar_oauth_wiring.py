from __future__ import annotations

from cryptography.fernet import Fernet

from platform_common.settings import AppSettings
from services.api.app.calendar.oauth import CalendarOAuthClient
from services.api.app.calendar.token_repository import CalendarTokenRepository
from services.api.app.main import (
    _build_calendar_oauth_client,
    _build_calendar_token_repository,
)


def test_build_token_repository_none_without_key(tmp_path):
    settings = AppSettings(
        calendar_db_path=str(tmp_path / "c.db"),
        calendar_token_encryption_key=None,
    )
    assert _build_calendar_token_repository(settings) is None


def test_build_token_repository_with_key(tmp_path):
    settings = AppSettings(
        calendar_db_path=str(tmp_path / "c.db"),
        calendar_token_encryption_key=Fernet.generate_key().decode("utf-8"),
    )
    repo = _build_calendar_token_repository(settings)
    assert isinstance(repo, CalendarTokenRepository)


def test_build_oauth_client_none_without_full_config():
    settings = AppSettings(
        google_oauth_client_id="cid",
        google_oauth_client_secret=None,
        google_oauth_redirect_uri="https://example.test/cb",
    )
    assert _build_calendar_oauth_client(settings) is None


def test_build_oauth_client_with_full_config():
    settings = AppSettings(
        google_oauth_client_id="cid",
        google_oauth_client_secret="secret",
        google_oauth_redirect_uri="https://example.test/cb",
    )
    client = _build_calendar_oauth_client(settings)
    assert isinstance(client, CalendarOAuthClient)
