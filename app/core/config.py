from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "BiletFlow API"
    app_env: Literal["development", "test", "production"] = "development"
    app_debug: bool = False
    app_api_v1_prefix: str = "/api/v1"
    app_database_url: str = "sqlite:///./biletflow.db"
    app_secret_key: str = "development-only-change-this-secret-key"
    app_access_token_minutes: int = 30
    app_frontend_url: str = "http://localhost:3000"
    app_activation_fee_minor: int = 100_000
    app_processing_fee_bps: int = 300
    app_ticket_hold_minutes: int = 15

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
