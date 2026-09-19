"""Unit of work over the SQLAlchemy async session."""

from __future__ import annotations

import time

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..domain.events import DomainEvent
from .models import OutboxRow
from .repositories import (
    SqlCommandRepository,
    SqlContactRepository,
    SqlConversationRepository,
    SqlMessageRepository,
)


class OutboxCollector:
    """Collect the events of the use case and write them to the outbox."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._events: list[DomainEvent] = []

    def collect(self, event: DomainEvent) -> None:
        """Add an event to those to record at the end of the use case."""
        self._events.append(event)

    async def flush(self) -> None:
        """Write the collected events to the outbox."""
        now = int(time.time())

        for event in self._events:
            self._session.add(
                OutboxRow(
                    event_id=event.event_id,
                    event_type=event.event_type,
                    event_version=event.event_version,
                    routing_key=event.routing_key,
                    correlation_id=event.correlation_id,
                    envelope=event.to_envelope(),
                    occurred_at=event.occurred_at,
                    created_at=now,
                )
            )

        self._events.clear()


class SqlAlchemyUnitOfWork:
    """Transactional boundary of a use case."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory
        self._session: AsyncSession | None = None

    async def __aenter__(self) -> SqlAlchemyUnitOfWork:
        self._session = self._session_factory()
        self.conversations = SqlConversationRepository(self._session)
        self.messages = SqlMessageRepository(self._session)
        self.contacts = SqlContactRepository(self._session)
        self.commands = SqlCommandRepository(self._session)
        self.events = OutboxCollector(self._session)
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        if self._session is None:
            return
        try:
            await self._session.rollback()
        finally:
            await self._session.close()
            self._session = None

    async def commit(self) -> None:
        """Record the collected events and commit the transaction."""
        if self._session is None:
            raise RuntimeError("The unit of work is not open.")
        await self.events.flush()
        await self._session.commit()

    async def rollback(self) -> None:
        """Roll back the transaction."""
        if self._session is not None:
            await self._session.rollback()
