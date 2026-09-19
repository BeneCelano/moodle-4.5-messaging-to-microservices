"""The readiness probe must not depend on the broker."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

# The probe module needs FastAPI and Pydantic: without them these tests are skipped.
pytest.importorskip("fastapi")

from fastapi import Response  # noqa: E402 - after the dependency check

from app.api.routers.health import readiness  # noqa: E402 - after the dependency check


class _BrokenSessions:
    """A session factory that fails as an unreachable database would."""

    async def __aenter__(self):
        raise RuntimeError("connection refused")

    async def __aexit__(self, *_) -> bool:
        return False


def _request(session_factory, connected: bool):
    """Compose the minimum the probe reads: settings, sessions and publisher."""
    state = SimpleNamespace(
        settings=SimpleNamespace(
            service_name="conversation-service", environment="test", publisher_enabled=True
        ),
        session_factory=session_factory,
        publisher=SimpleNamespace(is_connected=connected),
    )
    return SimpleNamespace(app=SimpleNamespace(state=state))


@pytest.mark.asyncio
async def test_ready_without_broker(session_factory) -> None:
    """With the database reachable and the broker not, the service stays available."""
    response = Response()

    outcome = await readiness(_request(session_factory, connected=False), response)

    assert response.status_code != 503
    assert outcome.status == "ok"
    # Reported, not applied.
    assert outcome.checks["broker"] == "disconnected"
    assert outcome.checks["database"] == "ok"


@pytest.mark.asyncio
async def test_not_ready_without_database(session_factory) -> None:
    """Without the database the service cannot serve, and must say so."""
    response = Response()

    outcome = await readiness(_request(lambda: _BrokenSessions(), connected=True), response)

    assert response.status_code == 503
    assert outcome.status == "degraded"
    assert outcome.checks["database"].startswith("error")


@pytest.mark.asyncio
async def test_ready(session_factory) -> None:
    """The normal case: the probe is quiet."""
    response = Response()

    outcome = await readiness(_request(session_factory, connected=True), response)

    assert response.status_code != 503
    assert outcome.checks == {"database": "ok", "broker": "ok"}
