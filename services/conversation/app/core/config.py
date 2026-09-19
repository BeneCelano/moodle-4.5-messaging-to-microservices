"""Service settings, read from the environment."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Service settings."""

    model_config = SettingsConfigDict(env_prefix="CS_", env_file=".env", extra="ignore")

    # Identity of the service.
    service_name: str = "conversation-service"
    environment: str = "lab"
    log_level: str = "INFO"

    # Persistence.
    database_url: str = "mysql+aiomysql://conversation_svc:conversation@127.0.0.1:3306/conversation_service"
    db_pool_size: int = 10
    db_max_overflow: int = 10
    db_pool_timeout: int = 5
    db_echo: bool = False

    # Broker.
    rabbitmq_url: str = "amqp://guest:guest@127.0.0.1:5672/"
    rabbitmq_exchange: str = "moodle.messaging"
    outbox_poll_interval_seconds: float = 0.5
    outbox_batch_size: int = 100
    outbox_max_attempts: int = 10
    publisher_enabled: bool = True

    # Authentication of incoming requests.
    api_token: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
