"""Service settings, read from the environment."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Service settings."""

    model_config = SettingsConfigDict(env_prefix="DG_", env_file=".env", extra="ignore")

    # Identity of the service.
    service_name: str = "delivery-gateway"
    environment: str = "lab"
    log_level: str = "INFO"

    # Persistence.
    database_url: str = "mysql+aiomysql://delivery_svc:delivery@127.0.0.1:3306/delivery_gateway"
    db_pool_size: int = 10
    db_max_overflow: int = 10
    db_pool_timeout: int = 5
    db_echo: bool = False

    # Broker. The queue is durable and named.
    rabbitmq_url: str = "amqp://guest:guest@127.0.0.1:5672/"
    rabbitmq_exchange: str = "moodle.messaging"
    rabbitmq_queue: str = "delivery-gateway"
    rabbitmq_routing_keys: str = "conversation.message.sent"

    # How many messages the broker hands to each consumer in advance.
    rabbitmq_prefetch: int = 16

    # Attempts.
    max_attempts: int = 5
    retry_base_seconds: float = 2.0
    retry_max_seconds: float = 60.0

    # Channel: mail. The mock lives on VM4.
    smtp_host: str = "192.168.72.74"
    smtp_port: int = 1025
    smtp_timeout_seconds: float = 10.0
    smtp_from_address: str = "noreply@moodle.lab"
    smtp_from_name: str = "Moodle"

    # Channel: airnotifier. The mock lives on VM4 too.
    airnotifier_url: str = "http://192.168.72.74:8081"
    airnotifier_app_id: str = "commin"
    airnotifier_token: str = ""
    airnotifier_timeout_seconds: float = 10.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
