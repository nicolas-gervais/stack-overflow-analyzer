from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="SOA_",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "Stack Overflow Analyzer"
    environment: str = "development"
    log_level: str = "INFO"
    database_url: str = "sqlite+aiosqlite:///./stack_overflow.db"
    max_period_days: int = Field(default=31, ge=1, le=366)

    stack_exchange_base_url: str = "https://api.stackexchange.com/2.3"
    stack_exchange_site: str = "stackoverflow"
    stack_exchange_timeout_seconds: float = Field(default=15.0, gt=0)
    stack_exchange_max_retries: int = Field(default=3, ge=0, le=8)

    openai_api_key: SecretStr | None = None
    openai_model: str = "gpt-5-mini"
    openai_timeout_seconds: float = Field(default=30.0, gt=0)


@lru_cache
def get_settings() -> Settings:
    return Settings()
