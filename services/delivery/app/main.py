"""Start-up of the service."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from .api.routers import health
from .application.deliver import Assignment, DeliveryService
from .core.config import get_settings
from .infrastructure.consumer import BrokerConsumer
from .infrastructure.processors import AirnotifierProcessor, EmailProcessor
from .infrastructure.views import ViewReader

logger = logging.getLogger(__name__)


def build_app() -> FastAPI:
    """Build the application with every dependency wired."""
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)

    engine = create_async_engine(
        settings.database_url,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_timeout=settings.db_pool_timeout,
        pool_pre_ping=True,
        echo=settings.db_echo,
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    processors = {
        "email": EmailProcessor(
            host=settings.smtp_host,
            port=settings.smtp_port,
            timeout_seconds=settings.smtp_timeout_seconds,
            from_address=settings.smtp_from_address,
            from_name=settings.smtp_from_name,
        ),
        "airnotifier": AirnotifierProcessor(
            base_url=settings.airnotifier_url,
            app_id=settings.airnotifier_app_id,
            token=settings.airnotifier_token,
            timeout_seconds=settings.airnotifier_timeout_seconds,
        ),
    }

    service = DeliveryService(
        session_factory=session_factory,
        views=ViewReader(),
        processors=processors,
        max_attempts=settings.max_attempts,
        retry_base_seconds=settings.retry_base_seconds,
        retry_max_seconds=settings.retry_max_seconds,
    )

    async def accept(envelope: dict) -> list[Assignment]:
        return await service.accept(envelope)

    async def execute(assignment: Assignment) -> float | None:
        outcome = await service.perform(assignment)
        return outcome.retry_after_seconds

    consumer = BrokerConsumer(
        url=settings.rabbitmq_url,
        exchange_name=settings.rabbitmq_exchange,
        queue_prefix=settings.rabbitmq_queue,
        routing_keys=settings.rabbitmq_routing_keys,
        prefetch=settings.rabbitmq_prefetch,
        on_event=accept,
        on_assignment=execute,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        """Open the subscription at start-up and close it at shutdown."""
        try:
            await consumer.start()
        except Exception:
            logger.exception("Subscription to the broker failed, going on and retrying")

        yield

        await consumer.close()
        await engine.dispose()

    app = FastAPI(title=settings.service_name, lifespan=lifespan)
    health.register(app, session_factory, consumer, service)
    return app


app = build_app()
