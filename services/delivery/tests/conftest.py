"""Test infrastructure for the two stages of the gateway."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence

import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.application.deliver import DeliveryService
from app.domain.channels import Recipient
from app.infrastructure.models import Base
from app.infrastructure.processors import Content, DeliveryFailure, PermanentFailure
from app.infrastructure.views import Device

COMPONENT = "moodle"
PROVIDER = "instantmessage"

# The minimal site settings for the channel resolution; without the `_locked` rows
# `resolve_channels` raises `UnknownProvider`.
SITE_DEFAULTS = {
    f"message_provider_{COMPONENT}_{PROVIDER}_enabled": "email,airnotifier",
    f"email_provider_{COMPONENT}_{PROVIDER}_locked": "0",
    f"airnotifier_provider_{COMPONENT}_{PROVIDER}_locked": "0",
}


def recipient(user_id: int = 10, **overrides) -> Recipient:
    """A deliverable recipient, with the variations the case asks for."""
    fields = {
        "user_id": user_id,
        "email": f"user{user_id}@invalid.example",
        "mail_format": 1,
        "auth_method": "manual",
        "suspended": 0,
        "deleted": 0,
        "email_stop": 0,
        "first_name": "User",
        "last_name": str(user_id),
    }
    fields.update(overrides)
    return Recipient(**fields)


def device(user_id: int = 10) -> Device:
    """An enabled device, so that the push channel is chosen for that user."""
    return Device(
        device_id=user_id * 100,
        user_id=user_id,
        app_id="com.moodle.moodlemobile",
        platform="Android",
        push_token=f"push-{user_id}",
        enabled=1,
    )


class FakeViews:
    """The four projections, declared by the case instead of read from the monolith."""

    def __init__(
        self,
        people: dict[int, Recipient] | None = None,
        preference_map: dict[int, dict[str, str]] | None = None,
        defaults: dict[str, str] | None = None,
        device_map: dict[int, list[Device]] | None = None,
    ) -> None:
        self.people = people if people is not None else {10: recipient(10)}
        self.preference_map = preference_map or {}
        self.defaults = defaults if defaults is not None else dict(SITE_DEFAULTS)
        self.device_map = device_map or {}

    async def recipients(self, session, user_ids: Sequence[int]) -> dict[int, Recipient]:
        """Return the declared recipients only: the others, for the gateway, do not
        exist.
        """
        return {uid: self.people[uid] for uid in user_ids if uid in self.people}

    async def preferences(self, session, user_ids: Sequence[int]) -> dict[int, dict[str, str]]:
        """Preferences per user."""
        return {uid: self.preference_map[uid] for uid in user_ids if uid in self.preference_map}

    async def site_defaults(self, session) -> dict[str, str]:
        return dict(self.defaults)

    async def devices(self, session, user_ids: Sequence[int]) -> dict[int, list[Device]]:
        """Devices per user."""
        return {uid: self.device_map[uid] for uid in user_ids if uid in self.device_map}


class FakeProcessor:
    """A channel that records what it is asked and fails on command: `DeliveryFailure` is a temporary
    fault to retry, `PermanentFailure` a definitive refusal.
    """

    def __init__(
        self,
        name: str,
        fault: Exception | None = None,
        configured: bool = True,
        requires_devices: bool = False,
    ) -> None:
        self.name = name
        self.fault = fault
        self.configured = configured
        self.requires_devices = requires_devices
        self.sent: list[tuple[int, Content]] = []

    def is_user_configured(self, recipient: Recipient, context: dict) -> bool:
        """Whether the channel is usable for that recipient."""
        if self.requires_devices and not context.get("devices"):
            return False
        return self.configured

    async def send(self, recipient: Recipient, content: Content, context: dict) -> None:
        """Record the send, or fail as the case asked."""
        if self.fault is not None:
            raise self.fault
        self.sent.append((recipient.user_id, content))


def envelope(
    event_id: str = "event-1",
    recipient_ids: Sequence[int] = (10,),
    **overrides,
) -> dict:
    """An envelope in the form the ConversationService publishes."""
    payload = {
        "message_id": 1,
        "conversation_id": 1,
        "conversation_type": 1,
        "user_id_from": 99,
        "recipient_ids": list(recipient_ids),
        "text": "hello",
        "message_format": 0,
        "time_created": 1_760_000_000,
        "source_component": COMPONENT,
        "source_event_name": PROVIDER,
    }
    payload.update(overrides.pop("payload", {}))
    envelope_dict = {
        "event_id": event_id,
        "event_type": "conversation.message.sent",
        "event_version": 1,
        "producer": "test",
        "payload": payload,
    }
    envelope_dict.update(overrides)
    return envelope_dict


@pytest_asyncio.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker]:
    """Create a clean schema for every test."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        # The processor register (seeded by a script in production); without it no
        # channel is chosen.
        for name in ("email", "airnotifier"):
            await connection.execute(
                text(
                    "INSERT INTO processor (name, enabled, updated_at)"
                    " VALUES (:n, 1, 1760000000)"
                ),
                {"n": name},
            )

    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    try:
        yield factory
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
def views() -> FakeViews:
    return FakeViews()


@pytest_asyncio.fixture
def processors() -> dict[str, FakeProcessor]:
    """The two channels, both working."""
    return {
        "email": FakeProcessor("email"),
        "airnotifier": FakeProcessor("airnotifier", requires_devices=True),
    }


@pytest_asyncio.fixture
def gateway(session_factory, views, processors) -> DeliveryService:
    """The service, with three attempts instead of five and negligible waits: a test must not measure
    time, phase 4 does on the real infrastructure.
    """
    return DeliveryService(
        session_factory=session_factory,
        views=views,
        processors=processors,
        max_attempts=3,
        retry_base_seconds=0.01,
        retry_max_seconds=0.02,
    )


__all__ = [
    "COMPONENT",
    "PROVIDER",
    "SITE_DEFAULTS",
    "DeliveryFailure",
    "Device",
    "PermanentFailure",
    "FakeProcessor",
    "FakeViews",
    "recipient",
    "device",
    "envelope",
]
