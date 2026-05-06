from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class AppSettings(BaseSettings):
    app_env: str = "development"
    log_level: str = "INFO"
    api_port: int = 8000
    web_ui_port: int = 8001
    bot_gateway_port: int = 8002
    ingest_worker_port: int = 8003
    scheduler_port: int = 8004
    qdrant_url: str = "http://qdrant:6333"
    database_url: str = "postgresql://postgres:postgres@postgres:5432/semantaix"
    persistence_db_path: str = ".data/semantaix_story1.db"
    telegram_bot_token: str = "replace-me"
    telegram_alert_username: str = "@ajdevy"
    telegram_alert_chat_id: str | None = None
    openrouter_api_key: str | None = None
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_model: str = "openai/gpt-4o-mini"
    incident_db_path: str = ".data/semantaix_incidents.db"
    incident_dedup_window_seconds: int = 300
    telegram_alert_username: str = "@ajdevy"
    telegram_alert_chat_id: str | None = None
    telegram_alert_debounce_seconds: int = 300

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


@lru_cache(maxsize=1)
def get_settings() -> AppSettings:
    return AppSettings()
