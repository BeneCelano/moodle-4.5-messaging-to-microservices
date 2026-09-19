"""Health probes and metrics endpoint."""

from __future__ import annotations

from contextlib import suppress

from fastapi import APIRouter, Request, Response, status
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text

from ..schemas import HealthOut

router = APIRouter(tags=["operations"])


@router.get("/healthz", response_model=HealthOut)
async def liveness(request: Request) -> HealthOut:
    """Whether the process is alive."""
    settings = request.app.state.settings
    return HealthOut(status="ok", service=settings.service_name, environment=settings.environment)


@router.get("/readyz", response_model=HealthOut)
async def readiness(request: Request, response: Response) -> HealthOut:
    """Whether the process can serve traffic."""
    settings = request.app.state.settings
    checks: dict[str, str] = {}

    try:
        async with request.app.state.session_factory() as session:
            await session.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as error:
        checks["database"] = f"error: {error}"

    publisher = getattr(request.app.state, "publisher", None)
    if publisher is None or not settings.publisher_enabled:
        checks["broker"] = "disabled"
    else:
        checks["broker"] = "ok" if publisher.is_connected else "disconnected"

    consumer = getattr(request.app.state, "consumer", None)
    if consumer is None or not settings.consumer_enabled:
        checks["consumer"] = "disabled"
    else:
        checks["consumer"] = "ok" if consumer.is_connected else "disconnected"

    # Templates are an installation step: the probe reports whether at least one exists.
    try:
        async with request.app.state.session_factory() as session:
            count = await session.execute(text("SELECT COUNT(*) FROM notification_templates"))
            total = int(count.scalar() or 0)
        checks["templates"] = f"{total}" if total else "missing"
    except Exception as error:
        checks["templates"] = f"error: {error}"

    # Readiness depends on the database only; broker and consumer are reported.
    ready = checks["database"] == "ok"
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return HealthOut(
        status="ok" if ready else "degraded",
        service=settings.service_name,
        environment=settings.environment,
        checks=checks,
    )


@router.get("/metrics")
async def metrics(request: Request) -> Response:
    """Expose the metrics in the format expected by the collector."""
    relay = getattr(request.app.state, "relay", None)
    if relay is not None:
        from ...core.observability import OUTBOX_PENDING

        # If the outbox depth cannot be read, the scrape goes on without it.
        with suppress(Exception):
            OUTBOX_PENDING.set(await relay.pending_count())

    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
