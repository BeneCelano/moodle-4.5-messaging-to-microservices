"""Access to the state owned by the service."""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .models import (
    STATUS_DEAD,
    STATUS_DELIVERED,
    STATUS_PENDING,
    DeadLetterRow,
    DeliveryRow,
    EventRow,
    ProcessorRow,
)


@dataclass(frozen=True, slots=True)
class DeliveryTarget:
    """A triple to deliver: event, recipient, channel."""

    event_id: str
    recipient_id: int
    channel: str
    component: str
    provider: str


def _insert_ignoring_duplicates(dialect_name: str, model: type):
    """Insert that does not fail on duplicates."""
    if dialect_name == "mysql":
        from sqlalchemy.dialects.mysql import insert as mysql_insert

        return mysql_insert(model)
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert

    return sqlite_insert(model)


def _dialect_of(session: AsyncSession) -> str:
    """Dialect name of the connection, falling back on sqlite for the tests."""
    return session.bind.dialect.name if session.bind is not None else "sqlite"


class SqlEventRepository:
    """The envelopes of the accepted events."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def remember(self, event_id: str, event_type: str, envelope: dict) -> None:
        """Keep the envelope, once."""
        dialect = _dialect_of(self._session)
        statement = _insert_ignoring_duplicates(dialect, EventRow).values(
            event_id=event_id,
            event_type=event_type,
            envelope=envelope,
            received_at=int(time.time()),
        )
        if dialect == "mysql":
            statement = statement.on_duplicate_key_update(event_id=statement.inserted.event_id)
        else:
            statement = statement.on_conflict_do_nothing()
        await self._session.execute(statement)

    async def envelope_of(self, event_id: str) -> dict | None:
        """The kept envelope, if any."""
        row = await self._session.get(EventRow, event_id)
        return row.envelope if row is not None else None


class SqlDeliveryRepository:
    """The delivery rows."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def claim(self, targets: Sequence[DeliveryTarget]) -> list[DeliveryRow]:
        """Record the triples to deliver and return those still to perform."""
        if not targets:
            return []

        now = int(time.time())
        dialect = _dialect_of(self._session)

        values = [
            {
                "event_id": t.event_id,
                "recipient_id": t.recipient_id,
                "channel": t.channel,
                "component": t.component,
                "provider": t.provider,
                "status": STATUS_PENDING,
                "attempts": 0,
                "next_attempt_at": now,
                "created_at": now,
                "updated_at": now,
            }
            for t in targets
        ]

        statement = _insert_ignoring_duplicates(dialect, DeliveryRow).values(values)
        if dialect == "mysql":
            # Updating the key with itself is the canonical way to say "do nothing".
            statement = statement.on_duplicate_key_update(event_id=statement.inserted.event_id)
        else:
            statement = statement.on_conflict_do_nothing()

        await self._session.execute(statement)

        event_ids = {t.event_id for t in targets}
        rows = await self._session.execute(
            select(DeliveryRow).where(
                DeliveryRow.event_id.in_(event_ids),
                DeliveryRow.status == STATUS_PENDING,
            )
        )
        return list(rows.scalars())

    async def due(self, limit: int) -> list[DeliveryRow]:
        """The deliveries to attempt now, oldest first, so that one postponed several times does not fall
        behind forever.
        """
        rows = await self._session.execute(
            select(DeliveryRow)
            .where(DeliveryRow.status == STATUS_PENDING, DeliveryRow.next_attempt_at <= int(time.time()))
            .order_by(DeliveryRow.next_attempt_at)
            .limit(limit)
        )
        return list(rows.scalars())

    async def mark_delivered(self, delivery_id: int, attempts: int) -> None:
        """Mark a delivery as done, recording how many attempts it took."""
        await self._session.execute(
            update(DeliveryRow)
            .where(DeliveryRow.id == delivery_id)
            .values(
                status=STATUS_DELIVERED,
                attempts=attempts,
                updated_at=int(time.time()),
                last_error=None,
            )
        )

    async def schedule_retry(self, delivery_id: int, attempts: int, error: str, next_at: int) -> None:
        """Postpone the delivery, recording the failed attempt."""
        await self._session.execute(
            update(DeliveryRow)
            .where(DeliveryRow.id == delivery_id)
            .values(
                attempts=attempts,
                next_attempt_at=next_at,
                last_error=error[:255],
                updated_at=int(time.time()),
            )
        )

    async def mark_dead(self, delivery_id: int, attempts: int, error: str) -> None:
        """Abandon the delivery."""
        await self._session.execute(
            update(DeliveryRow)
            .where(DeliveryRow.id == delivery_id)
            .values(
                status=STATUS_DEAD,
                attempts=attempts,
                last_error=error[:255],
                updated_at=int(time.time()),
            )
        )

    async def counts_by_status(self) -> dict[str, int]:
        """How many deliveries per status."""
        rows = await self._session.execute(
            select(DeliveryRow.status, func.count()).group_by(DeliveryRow.status)
        )
        return {status: count for status, count in rows.all()}


class SqlDeadLetterRepository:
    """The store of the abandoned deliveries."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(
        self,
        *,
        event_id: str,
        recipient_id: int,
        channel: str,
        envelope: dict,
        reason: str,
        attempts: int,
    ) -> None:
        """Keep the envelope, not a reference to it: without it the row would say that something failed
        without saying what.
        """
        self._session.add(
            DeadLetterRow(
                event_id=event_id,
                recipient_id=recipient_id,
                channel=channel,
                envelope=envelope,
                reason=reason[:255],
                attempts=attempts,
                created_at=int(time.time()),
            )
        )

    async def count(self) -> int:
        """How many deliveries were abandoned."""
        rows = await self._session.execute(select(func.count()).select_from(DeadLetterRow))
        return int(rows.scalar_one())


class SqlProcessorRepository:
    """The register of the outbound processors."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def enabled_names(self) -> list[str]:
        """Names of the enabled processors, `get_message_processors(true)` of the monolith, ordered by name
        so that the resolved set is deterministic.
        """
        rows = await self._session.execute(
            select(ProcessorRow).where(ProcessorRow.enabled == 1).order_by(ProcessorRow.name)
        )
        return [row.name for row in rows.scalars()]

    async def upsert(self, name: str, enabled: int) -> None:
        """Register or update a processor."""
        existing = await self._session.get(ProcessorRow, name)
        if existing is None:
            self._session.add(ProcessorRow(name=name, enabled=enabled, updated_at=int(time.time())))
        else:
            existing.enabled = enabled
            existing.updated_at = int(time.time())
