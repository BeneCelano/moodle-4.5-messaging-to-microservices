"""Service settings, read from the environment."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Service settings."""

    model_config = SettingsConfigDict(env_prefix="NS_", env_file=".env", extra="ignore")

    # Identity of the service.
    service_name: str = "notification-service"
    environment: str = "lab"
    log_level: str = "INFO"

    # Persistence.
    database_url: str = "mysql+aiomysql://notification_svc:notification@127.0.0.1:3306/notification_service"
    db_pool_size: int = 10
    db_max_overflow: int = 10
    db_pool_timeout: int = 5
    db_echo: bool = False

    # Broker, publishing side.
    rabbitmq_url: str = "amqp://guest:guest@127.0.0.1:5672/"
    rabbitmq_exchange: str = "moodle.messaging"
    outbox_poll_interval_seconds: float = 0.5
    outbox_batch_size: int = 100
    outbox_max_attempts: int = 10
    publisher_enabled: bool = True

    # Broker, consuming side. The queue is durable and named.
    consumer_enabled: bool = True
    rabbitmq_queue: str = "notification-service.events"
    rabbitmq_routing_keys: str = "conversation.contactrequest.created"
    rabbitmq_prefetch: int = 16

    # Authentication of incoming requests.
    api_token: str = ""

    # How long the site settings read from the view are cached.
    site_settings_ttl_seconds: int = 300

    # Public address of the site, used in the links of the notifications (`wwwroot`
    # lives in config.php, not in `mdl_config`).
    site_wwwroot: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
