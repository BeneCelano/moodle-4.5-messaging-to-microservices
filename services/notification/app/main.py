"""Composition root of the service."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .api.routers import health, notifications
from .application.contact_request import ContactRequestHandler
from .application.notification_service import NotificationService
from .core.config import get_settings
from .core.db import SystemClock, create_engine, create_session_factory
from .core.errors import register_exception_handlers
from .core.logging import configure_logging, request_id_var
from .core.observability import (
    EVENTS_CONSUMED,
    EVENTS_PUBLISH_FAILED,
    EVENTS_PUBLISHED,
    NOTIFICATIONS_PERSISTED,
    REQUEST_DURATION,
    REQUESTS_IN_FLIGHT,
)
from .infrastructure.consumer import BrokerConsumer, MalformedEvent
from .infrastructure.publisher import RabbitMQPublisher
from .infrastructure.relay import OutboxRelay, RelayMetrics
from .infrastructure.uow import SqlAlchemyUnitOfWork
from .infrastructure.views import ViewReader

logger = logging.getLogger(__name__)


class NotificationCounter:
    """Satisfies the NotificationMetrics port with the Prometheus counter, so that the application layer
    does not know which metrics library exists.
    """

    def __init__(self, counter) -> None:
        self._counter = counter

    def persisted(self, component: str) -> None:
        self._counter.labels(component=component).inc()



BROKER_FIRST_WAIT = 2.0
"""Seconds before the first new attempt to reach the broker."""

BROKER_MAX_WAIT = 30.0
"""Cap on the wait between two attempts."""


async def retry_until_success(operation: Callable[[], Awaitable[None]], description: str) -> None:
    """Repeat a start-up operation until it succeeds, doubling the wait up to a cap."""
    wait = BROKER_FIRST_WAIT
    while True:
        try:
            await operation()
            logger.info("%s: succeeded", description)
            return
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.warning("%s: %s. Retrying in %.0fs", description, error, wait)
            await asyncio.sleep(wait)
            wait = min(wait * 2, BROKER_MAX_WAIT)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Build the collaborators at start-up and close them in order at shutdown."""
    settings = get_settings()
    configure_logging(settings.log_level, settings.service_name)

    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    clock = SystemClock()

    def uow_factory() -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(session_factory)

    views = ViewReader(session_factory, settings.site_settings_ttl_seconds, settings.site_wwwroot)
    notification_counter = NotificationCounter(NOTIFICATIONS_PERSISTED)
    notification_service = NotificationService(uow_factory, clock, metrics=notification_counter)
    contact_requests = ContactRequestHandler(uow_factory, clock, views, metrics=notification_counter)

    publisher = RabbitMQPublisher(settings.rabbitmq_url, settings.rabbitmq_exchange)
    relay: OutboxRelay | None = None
    publish_retry: asyncio.Task | None = None
    consume_retry: asyncio.Task | None = None

    def start_relay() -> None:
        """Build and start the relay on an established connection."""
        nonlocal relay
        relay = OutboxRelay(
            session_factory=session_factory,
            publisher=publisher,
            poll_interval=settings.outbox_poll_interval_seconds,
            batch_size=settings.outbox_batch_size,
            max_attempts=settings.outbox_max_attempts,
            metrics=RelayMetrics(EVENTS_PUBLISHED, EVENTS_PUBLISH_FAILED),
        )
        relay.start()
        app.state.relay = relay

    if settings.publisher_enabled:
        try:
            await publisher.connect()
            start_relay()
        except Exception as error:
            # Events stay in the outbox; the task below keeps retrying the connection.
            logger.warning(
                "The broker does not answer at start-up: %s. Retrying in the background", error
            )

            async def connect_later() -> None:
                await retry_until_success(publisher.connect, "Broker connection")
                start_relay()

            publish_retry = asyncio.create_task(connect_later(), name="broker-connect-retry")

    async def consume(envelope: dict) -> None:
        """Route the event to the handler that knows it, and record the outcome."""
        event_type = envelope.get("event_type") or "unknown"
        try:
            await contact_requests.handle(envelope)
        except MalformedEvent:
            EVENTS_CONSUMED.labels(event_type=event_type, outcome="malformed").inc()
            raise
        except Exception:
            EVENTS_CONSUMED.labels(event_type=event_type, outcome="failed").inc()
            raise
        EVENTS_CONSUMED.labels(event_type=event_type, outcome="ok").inc()

    consumer: BrokerConsumer | None = None
    if settings.consumer_enabled:
        consumer = BrokerConsumer(
            url=settings.rabbitmq_url,
            exchange_name=settings.rabbitmq_exchange,
            queue_name=settings.rabbitmq_queue,
            routing_keys=settings.rabbitmq_routing_keys,
            prefetch=settings.rabbitmq_prefetch,
            on_event=consume,
        )
        try:
            await consumer.start()
        except Exception as error:
            # The durable queue keeps the events published while the service is not
            # listening; the task below keeps retrying the connection.
            logger.warning(
                "Subscription to the broker failed: %s. Retrying in the background", error
            )
            consume_retry = asyncio.create_task(
                retry_until_success(consumer.start, "Broker subscription"),
                name="broker-subscribe-retry",
            )

    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.uow_factory = uow_factory
    app.state.clock = clock
    app.state.views = views
    app.state.publisher = publisher
    app.state.relay = relay
    app.state.consumer = consumer
    app.state.notification_service = notification_service
    app.state.contact_requests = contact_requests

    logger.info("Service started in environment '%s'", settings.environment)

    try:
        yield
    finally:
        for task in (publish_retry, consume_retry):
            if task is not None:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
        if consumer is not None:
            await consumer.close()
        if relay is not None:
            await relay.stop()
        await publisher.close()
        await engine.dispose()
        logger.info("Service stopped")


def create_app() -> FastAPI:
    """Build the application."""
    app = FastAPI(
        title="NotificationService",
        version="0.1.0",
        summary="Notification bounded context extracted from Moodle's message subsystem",
        lifespan=lifespan,
    )

    @app.middleware("http")
    async def observe_request(request: Request, call_next):
        """Propagate the request identifier and measure the response time."""
        correlation_id = request.headers.get("X-Request-Id") or uuid.uuid4().hex[:16]
        token = request_id_var.set(correlation_id)
        started = time.perf_counter()
        REQUESTS_IN_FLIGHT.inc()

        try:
            response = await call_next(request)
            status_code = response.status_code
        except Exception:
            status_code = 500
            REQUEST_DURATION.labels(
                method=request.method, route=_route_of(request), status="500"
            ).observe(time.perf_counter() - started)
            REQUESTS_IN_FLIGHT.dec()
            request_id_var.reset(token)
            raise

        REQUEST_DURATION.labels(
            method=request.method, route=_route_of(request), status=str(status_code)
        ).observe(time.perf_counter() - started)
        REQUESTS_IN_FLIGHT.dec()
        response.headers["X-Request-Id"] = correlation_id
        request_id_var.reset(token)
        return response

    @app.exception_handler(Exception)
    async def unhandled_error(request: Request, exc: Exception) -> JSONResponse:
        """Log the unexpected error and answer without exposing internals."""
        logger.exception("Unhandled error while serving %s %s", request.method, request.url.path)
        return JSONResponse(status_code=500, content={"detail": "Internal server error."})

    register_exception_handlers(app)

    app.include_router(health.router)
    app.include_router(notifications.router)

    return app


def _route_of(request: Request) -> str:
    """Route template, not the concrete path: labelling with the path would grow the time series
    with the number of users.
    """
    route = request.scope.get("route")
    return getattr(route, "path", request.url.path)


app = create_app()
