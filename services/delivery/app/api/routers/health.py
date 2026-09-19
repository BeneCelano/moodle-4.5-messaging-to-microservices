"""Health probes and metrics."""

from __future__ import annotations

from fastapi import APIRouter, Response, status
from prometheus_client import CONTENT_TYPE_LATEST, Gauge, generate_latest
from sqlalchemy import text

from ...infrastructure.models import STATUS_DEAD, STATUS_DELIVERED, STATUS_PENDING

router = APIRouter(tags=["health"])

deliveries_by_status = Gauge(
    "delivery_gateway_deliveries",
    "Deliveries recorded, by status",
    ["status"],
)

# The statuses the gauge declares at every scrape, zero included.
EXPOSED_STATUSES = (STATUS_PENDING, STATUS_DELIVERED, STATUS_DEAD, "dead_letter")


def register(app, session_factory, consumer, service) -> None:
    """Attach the probes to the application, with the dependencies already built."""

    @router.get("/healthz")
    async def healthz() -> dict:
        """The process is alive."""
        return {"status": "ok"}

    @router.get("/readyz")
    async def readyz(response: Response) -> dict:
        """The service is able to work."""
        checks = {"database": "unknown", "broker": "disconnected"}

        try:
            async with session_factory() as session:
                await session.execute(text("SELECT 1"))
            checks["database"] = "ok"
        except Exception as error:
            checks["database"] = f"error: {error}"

        checks["broker"] = "ok" if consumer.is_connected else "disconnected"

        # Readiness depends on the database only; the broker state is reported.
        ready = checks["database"] == "ok"
        if not ready:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

        return {"ready": ready, "checks": checks}

    @router.get("/metrics")
    async def metrics() -> Response:
        """Metrics in Prometheus format."""
        try:
            counts = await service.counts()
            # First the known statuses, all of them, zero included.
            for status in EXPOSED_STATUSES:
                deliveries_by_status.labels(status=status).set(counts.get(status, 0))
            # Then any unexpected status.
            for status, count in counts.items():
                if status not in EXPOSED_STATUSES:
                    deliveries_by_status.labels(status=status).set(count)
        except Exception:
            pass

        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    app.include_router(router)
