from functools import lru_cache
from typing import Literal

from pydantic import model_validator
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
    app_cors_origins: list[str] = []
    app_activation_fee_minor: int = 100_000
    app_processing_fee_bps: int = 300
    app_ticket_hold_minutes: int = 15
    app_email_verification_hours: int = 24
    app_password_reset_minutes: int = 30

    # "console" logs messages, "memory" keeps them in-process for tests, "smtp" delivers them.
    email_backend: Literal["console", "memory", "smtp"] = "console"
    email_from_address: str = "no-reply@biletflow.kz"
    email_from_name: str = "BiletFlow"
    email_reply_to: str | None = None
    smtp_host: str = "localhost"
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    # "starttls" for port 587, "ssl" for port 465, "none" only for local relays such as Mailpit.
    smtp_security: Literal["starttls", "ssl", "none"] = "starttls"
    smtp_timeout_seconds: int = 20

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @model_validator(mode="after")
    def validate_production(self):
        if self.app_env == "production":
            if len(self.app_secret_key) < 32 or self.app_secret_key.startswith("development"):
                raise ValueError("APP_SECRET_KEY must be a unique value of 32+ characters")
            if self.email_backend != "smtp":
                raise ValueError("EMAIL_BACKEND must be smtp in production")
        return self

    @property
    def frontend_url(self) -> str:
        return self.app_frontend_url.rstrip("/")

    @property
    def exposes_demo_tokens(self) -> bool:
        """Expose one-time tokens in response headers only when no real email is sent."""
        return self.app_env != "production" and self.email_backend != "smtp"


@lru_cache
def get_settings() -> Settings:
    return Settings()
