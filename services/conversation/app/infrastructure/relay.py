"""Delivery of the events recorded in the outbox."""

from __future__ import annotations

import asyncio
import logging
import time

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from .models import OutboxRow
from .publisher import RabbitMQPublisher

logger = logging.getLogger(__name__)


class OutboxRelay:
    """Deliver the events in the outbox to the broker."""

    def __init__(
        self,
        session_factory: async_sessionmaker,
        publisher: RabbitMQPublisher,
        poll_interval: float,
        batch_size: int,
        max_attempts: int,
        metrics: RelayMetrics | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._publisher = publisher
        self._poll_interval = poll_interval
        self._batch_size = batch_size
        self._max_attempts = max_attempts
        self._metrics = metrics
        self._task: asyncio.Task | None = None
        self._stopping = asyncio.Event()

    def start(self) -> None:
        """Start the background task."""
        self._stopping.clear()
        self._task = asyncio.create_task(self._run(), name="outbox-relay")

    async def stop(self) -> None:
        """Ask for the stop and wait for the current cycle to end."""
        self._stopping.set()
        if self._task is not None:
            await asyncio.gather(self._task, return_exceptions=True)

    async def _run(self) -> None:
        """Loop until the stop is requested."""
        while not self._stopping.is_set():
            try:
                delivered = await self.drain_once()
            except Exception:
                logger.exception("The outbox relay cycle failed")
                delivered = 0

            if delivered == 0:
                try:
                    await asyncio.wait_for(self._stopping.wait(), timeout=self._poll_interval)
                except TimeoutError:
                    continue

    async def drain_once(self) -> int:
        """Publish one batch of events and return how many were delivered."""
        async with self._session_factory() as session:
            statement = (
                select(OutboxRow)
                .where(OutboxRow.published_at.is_(None), OutboxRow.attempts < self._max_attempts)
                .order_by(OutboxRow.id.asc())
                .limit(self._batch_size)
                .with_for_update(skip_locked=True)
            )
            result = await session.execute(statement)
            rows = list(result.scalars().all())

            delivered = 0
            for row in rows:
                try:
                    await self._publisher.publish(row.routing_key, row.envelope, row.event_id)
                except Exception as error:
                    logger.warning("Publication of event %s failed: %s", row.event_id, error)
                    await session.execute(
                        update(OutboxRow)
                        .where(OutboxRow.id == row.id)
                        .values(attempts=row.attempts + 1, last_error=str(error)[:1000])
                    )
                    if self._metrics is not None:
                        self._metrics.publish_failed(row.event_type)
                    continue

                await session.execute(
                    update(OutboxRow)
                    .where(OutboxRow.id == row.id)
                    .values(published_at=int(time.time()), attempts=row.attempts + 1)
                )
                delivered += 1
                if self._metrics is not None:
                    self._metrics.published(row.event_type)

            await session.commit()
            return delivered

    async def pending_count(self) -> int:
        """Count the events still to deliver, for observability."""
        async with self._session_factory() as session:
            result = await session.execute(
                select(OutboxRow.id).where(OutboxRow.published_at.is_(None)).limit(10000)
            )
            return len(result.all())


class RelayMetrics:
    """Counters exposed by the relay, injected so as not to tie the relay to the metrics
    library.
    """

    def __init__(self, published, failed) -> None:
        self._published = published
        self._failed = failed

    def published(self, event_type: str) -> None:
        """Record a delivered event."""
        self._published.labels(event_type=event_type).inc()

    def publish_failed(self, event_type: str) -> None:
        """Record a failed delivery attempt."""
        self._failed.labels(event_type=event_type).inc()
