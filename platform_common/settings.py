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
    telegram_alert_debounce_seconds: int = 300
    hitl_ticket_db_path: str = ".data/semantaix_hitl.db"
    hitl_primary_operator_username: str = "@ajdevy"
    hitl_primary_operator_chat_id: str | None = None
    rag_db_path: str = ".data/semantaix_rag.db"
    knowledge_db_path: str = ".data/semantaix_knowledge.db"
    hitl_config_admin_username: str = "@ajdevy"
    answer_trace_db_path: str = ".data/semantaix_answer_traces.db"
    answer_trace_snippet_max_chars: int = 240
    nl_ops_enabled: bool = True
    nl_ops_db_path: str = ".data/semantaix_nl_ops.db"
    nl_ops_admin_user_ids: str = ""
    nl_ops_default_tenant_id: str = "default"
    backup_db_path: str = ".data/semantaix_backups.db"
    backup_archive_dir: str = ".data/backups"
    backup_source_paths: str = (
        ".data/semantaix_story1.db,"
        ".data/semantaix_incidents.db,"
        ".data/semantaix_hitl.db,"
        ".data/semantaix_rag.db,"
        ".data/semantaix_knowledge.db"
    )

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    def backup_source_path_list(self) -> list[str]:
        return [path.strip() for path in self.backup_source_paths.split(",") if path.strip()]

    def nl_ops_admin_user_id_list(self) -> list[str]:
        return [item.strip() for item in self.nl_ops_admin_user_ids.split(",") if item.strip()]


@lru_cache(maxsize=1)
def get_settings() -> AppSettings:
    return AppSettings()
