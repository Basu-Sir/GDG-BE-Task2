"""Application configuration loaded from environment variables."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Central configuration.  Values come from environment / .env file."""

    # ── Database ──────────────────────────────────────────────────────
    database_url: str = "postgresql+asyncpg://workshop_app:local_dev_password@db:5432/workshops"
    database_url_sync: str = "postgresql+psycopg://workshop_app:local_dev_password@db:5432/workshops"

    # ── Mock notifier ─────────────────────────────────────────────────
    mock_notifier_url: str = "http://mock-notifier:8001"

    # ── Worker ────────────────────────────────────────────────────────
    worker_poll_seconds: float = 1.0
    worker_batch_size: int = 10
    worker_http_timeout_seconds: float = 2.0
    worker_lease_seconds: int = 15

    # ── Retry policy ──────────────────────────────────────────────────
    retry_base_seconds: float = 1.0
    retry_max_seconds: float = 60.0

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
