from functools import lru_cache
from pathlib import Path
from typing import ClassVar

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    project_root: ClassVar[Path] = Path(__file__).resolve().parents[2]
    model_config = SettingsConfigDict(
        env_file=project_root / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    app_name: str = "Moltbook Backend"
    app_env: str = "dev"
    debug: bool = True
    api_v1_prefix: str = "/api/v1"

    postgres_db: str = "moltbook"
    postgres_user: str = "postgres"
    postgres_password: str = "postgres"
    postgres_host: str = "127.0.0.1"
    postgres_port: int = 5432
    database_url: str | None = None
    auth_secret_key: str = "change-me-in-production"
    auth_access_token_expire_minutes: int = 10080
    human_daily_thread_limit: int = Field(default=5, ge=1)

    cors_origins: list[str] = ["*"]
    frontend_base_url: str | None = None

    # Sensitive word service
    sensitive_word_service_url: str = "http://localhost:8200"
    sensitive_word_llm_key: str | None = None  # autocheck 专用 key，不与主 AI 功能共享

    # AI summary
    openai_api_key: str | None = None
    openai_api_base: str = "https://openrouter.ai/api/v1"
    openai_default_model: str = "x-ai/grok-4.1-fast"
    summary_threshold_zh: int = 270
    summary_threshold_en: int = 580
    summary_interval_minutes: int = 180
    summary_lock_key: int = 20260304

    # DM push assistant
    dm_push_assistant_enabled: bool = False
    dm_push_assistant_start_time: str | None = None
    dm_push_assistant_interval_minutes: int = 360
    dm_push_assistant_user_batch_size: int = 100
    dm_push_assistant_dedupe_hours: int = 24
    dm_push_assistant_lock_key: int = 20260303
    dm_push_assistant_target_user_id: int | None = None
    dm_push_assistant_target_username: str | None = None
    dm_push_assistant_assistant_id: int | None = None

    @computed_field
    @property
    def sqlalchemy_database_uri(self) -> str:
        if self.database_url:
            if self.database_url.startswith("postgresql://"):
                return self.database_url.replace(
                    "postgresql://", "postgresql+psycopg://", 1
                )
            return self.database_url
        return (
            f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
