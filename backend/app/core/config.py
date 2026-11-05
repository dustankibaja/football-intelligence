from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Application Configuration Settings loaded from environment variables or .env file.
    """
    APP_ENV: str = "development"
    APP_NAME: str = "football-intelligence-system"
    DEBUG: bool = True
    LOG_LEVEL: str = "INFO"

    # PostgreSQL Connection Parameters
    POSTGRES_SERVER: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str = "postgres"
    POSTGRES_PASSWORD: str = "postgres"
    POSTGRES_DB: str = "football_intelligence"

    # Optional Database DSN Override
    DATABASE_URL_OVERRIDE: str | None = None

    # Future Live Provider Config Placeholders (Unused in MVP-A)
    LIVE_DATA_API_KEY: str | None = None
    LIVE_DATA_API_BASE_URL: str | None = None

    # Explicitly opt-in customer API; never expose unverified predictions.
    CUSTOMER_API_TOKEN: str | None = None
    CUSTOMER_ALLOWED_ORIGINS: str = ""

    # Operational Watchdog Staleness Thresholds (Hours)
    OPS_RUN_STALE_AFTER_HOURS: float = 24.0
    OPS_PREDICTION_STALE_AFTER_HOURS: float = 48.0

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    @property
    def database_url(self) -> str:
        if self.DATABASE_URL_OVERRIDE:
            return self.DATABASE_URL_OVERRIDE
        return (
            f"postgresql+psycopg2://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_SERVER}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
