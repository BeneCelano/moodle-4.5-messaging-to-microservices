"""Test infrastructure."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.application.contact_request import ContactRequestHandler
from app.application.notification_service import NotificationService
from app.domain.entities import NotificationTemplate, Recipient
from app.infrastructure.models import Base
from app.infrastructure.uow import SqlAlchemyUnitOfWork

NOW = 1_760_000_000


class FrozenClock:
    """Frozen clock, to assert on the recorded times."""

    def __init__(self, value: int = NOW) -> None:
        self.value = value

    def now(self) -> int:
        return self.value

    def advance(self, seconds: int) -> None:
        """Move the time forward."""
        self.value += seconds


class FakeViews:
    """Double of the two views on the monolith schema."""

    def __init__(
        self,
        people: dict[int, Recipient] | None = None,
        settings: dict[str, str] | None = None,
    ) -> None:
        self.people = people if people is not None else {}
        self.settings = (
            settings
            if settings is not None
            else {
                "sitename": "Test Moodle",
                "wwwroot": "https://moodle.lab",
                "fullnamedisplay": "language",
            }
        )
        self.reads = 0

    async def recipients(self, user_ids: list[int]) -> dict[int, Recipient]:
        """Return the known people among those requested."""
        return {i: self.people[i] for i in user_ids if i in self.people}

    async def site_settings(self) -> dict[str, str]:
        """Return the site settings, counting how many times they were asked."""
        self.reads += 1
        return dict(self.settings)


def person(
    user_id: int,
    *,
    first_name: str = "Anna",
    last_name: str = "Rossi",
    lang: str = "it",
    email_stop: bool = False,
    deleted: bool = False,
) -> Recipient:
    return Recipient(
        user_id=user_id,
        first_name=first_name,
        last_name=last_name,
        lang=lang,
        email_stop=email_stop,
        deleted=deleted,
    )


@pytest_asyncio.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker]:
    """Create a clean schema for every test, with the constraints on."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    @event.listens_for(engine.sync_engine, "connect")
    def _enable_foreign_keys(connection, _record):
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    try:
        yield factory
    finally:
        await engine.dispose()


@pytest.fixture
def clock() -> FrozenClock:
    return FrozenClock()


@pytest.fixture
def uow_factory(session_factory):
    """Factory of units of work over the in-memory engine."""

    def make() -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(session_factory)

    return make


@pytest.fixture
def service(uow_factory, clock) -> NotificationService:
    return NotificationService(uow_factory, clock)


@pytest.fixture
def views() -> FakeViews:
    return FakeViews(people={7: person(7), 9: person(9, first_name="Bruno", last_name="Verdi")})


@pytest.fixture
def handler(uow_factory, clock, views) -> ContactRequestHandler:
    return ContactRequestHandler(uow_factory, clock, views)


@pytest_asyncio.fixture
async def seeded_templates(uow_factory, clock):
    """Seed the contact request templates, in Italian and in English."""
    async with uow_factory() as uow:
        await uow.templates.upsert(
            NotificationTemplate(
                component="moodle",
                event_type="messagecontactrequests",
                lang="it",
                subject="{$a->sitename}: richiesta di contatto da {$a->user}",
                body=(
                    '{$a->user} chiede di essere aggiunto ai contatti. Vai alla pagina delle '
                    '<a href="{$a->url}">richieste di contatto</a> per rispondere'
                ),
                custom_data='{"accept": "Accetta", "reject": "Rifiuta"}',
            ),
            clock.now(),
        )
        await uow.templates.upsert(
            NotificationTemplate(
                component="moodle",
                event_type="messagecontactrequests",
                lang="en",
                subject="{$a->sitename}: Contact request from {$a->user}",
                body="{$a->user} is requesting to be added as a contact",
                custom_data='{"accept": "Accept", "reject": "Reject"}',
            ),
            clock.now(),
        )
        await uow.commit()


def contact_request_envelope(
    *,
    event_id: str = "11111111-1111-1111-1111-111111111111",
    user_id: int = 9,
    requested_user_id: int = 7,
    event_type: str = "conversation.contactrequest.created",
) -> dict:
    """Build an envelope like the one the ConversationService publishes."""
    return {
        "event_id": event_id,
        "event_type": event_type,
        "event_version": 1,
        "occurred_at": "2026-09-03T10:00:00+00:00",
        "producer": "conversation-service",
        "correlation_id": None,
        "payload": {
            "request_id": 42,
            "user_id": user_id,
            "requested_user_id": requested_user_id,
            "time_created": NOW,
            "source_component": "moodle",
            "source_event_name": "messagecontactrequests",
        },
    }
