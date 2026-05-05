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
    telegram_bot_token: str = "replace-me"
    openrouter_api_key: str | None = None
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_model: str = "openai/gpt-4o-mini"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


@lru_cache(maxsize=1)
def get_settings() -> AppSettings:
    return AppSettings()
