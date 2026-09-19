"""Test infrastructure."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.application.contact_service import ContactService
from app.application.conversation_service import ConversationService
from app.application.message_service import MessageService
from app.infrastructure.models import Base, UserViewRow
from app.infrastructure.uow import SqlAlchemyUnitOfWork


class FrozenClock:
    """Frozen clock, to assert on the recorded times."""

    def __init__(self, value: int = 1_760_000_000) -> None:
        self.value = value

    def now(self) -> int:
        return self.value

    def advance(self, seconds: int) -> None:
        """Move the time forward."""
        self.value += seconds


@pytest_asyncio.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker]:
    """Create a clean schema for every test."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

        # `user_view` is a view in production and an empty table here: the users are
        # inserted.
        await connection.execute(
            UserViewRow.__table__.insert(),
            [
                {
                    "user_id": user_id,
                    "full_name": f"User {user_id}",
                    "picture_url": None,
                    "deleted": 0,
                    "updated_at": 1_760_000_000,
                }
                for user_id in range(1, 51)
            ],
        )

    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    try:
        yield factory
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
def uow_factory(session_factory):
    """Factory of units of work."""

    def factory() -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(session_factory)

    return factory


@pytest_asyncio.fixture
def clock() -> FrozenClock:
    return FrozenClock()


@pytest_asyncio.fixture
def conversations(uow_factory, clock) -> ConversationService:
    return ConversationService(uow_factory, clock)


@pytest_asyncio.fixture
def messages(uow_factory, clock) -> MessageService:
    return MessageService(uow_factory, clock)


@pytest_asyncio.fixture
def contacts(uow_factory, clock) -> ContactService:
    return ContactService(uow_factory, clock)
