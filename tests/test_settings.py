from platform_common.settings import AppSettings


def test_settings_defaults():
    settings = AppSettings()
    assert settings.app_env == "development"
    assert settings.log_level == "INFO"
    assert settings.api_port == 8000
    assert settings.openrouter_base_url == "https://openrouter.ai/api/v1"
    assert settings.incident_db_path == ".data/semantaix_incidents.db"
    assert settings.incident_dedup_window_seconds == 300
    assert settings.telegram_alert_username == "@ajdevy"
    assert settings.telegram_alert_chat_id is None


def test_settings_env_override(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("API_PORT", "9000")
    monkeypatch.setenv("OPENROUTER_MODEL", "anthropic/claude-3.5-sonnet")
    monkeypatch.setenv("INCIDENT_DB_PATH", ".tmp/incidents.sqlite3")
    monkeypatch.setenv("INCIDENT_DEDUP_WINDOW_SECONDS", "60")
    monkeypatch.setenv("TELEGRAM_ALERT_USERNAME", "@ops")
    monkeypatch.setenv("TELEGRAM_ALERT_CHAT_ID", "-1001234")
    settings = AppSettings()
    assert settings.app_env == "test"
    assert settings.log_level == "DEBUG"
    assert settings.api_port == 9000
    assert settings.openrouter_model == "anthropic/claude-3.5-sonnet"
    assert settings.incident_db_path == ".tmp/incidents.sqlite3"
    assert settings.incident_dedup_window_seconds == 60
    assert settings.telegram_alert_username == "@ops"
    assert settings.telegram_alert_chat_id == "-1001234"
