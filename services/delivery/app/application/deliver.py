"""The two use cases of the gateway."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..domain.channels import Recipient, UnknownProvider, resolve_channels
from ..infrastructure.processors import Content, DeliveryFailure, PermanentFailure, Processor
from ..infrastructure.repositories import (
    DeliveryTarget,
    SqlDeadLetterRepository,
    SqlDeliveryRepository,
    SqlEventRepository,
    SqlProcessorRepository,
)
from ..infrastructure.views import ViewReader

logger = logging.getLogger(__name__)


class MalformedEvent(Exception):
    """The envelope cannot be interpreted."""


@dataclass(frozen=True, slots=True)
class Assignment:
    """A delivery assignment, to be queued."""

    delivery_id: int
    event_id: str
    recipient_id: int
    channel: str


@dataclass(frozen=True, slots=True)
class Attempt:
    """Outcome of an attempt."""

    delivered: bool
    retry_after_seconds: float | None
    reason: str


class DeliveryService:
    """Accept the events and perform the deliveries."""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        views: ViewReader,
        processors: dict[str, Processor],
        max_attempts: int,
        retry_base_seconds: float,
        retry_max_seconds: float,
    ) -> None:
        self._session_factory = session_factory
        self._views = views
        self._processors = processors
        self._max_attempts = max_attempts
        self._retry_base = retry_base_seconds
        self._retry_max = retry_max_seconds

    # -- first stage ---------------------------------------------------------------------------

    async def accept(self, envelope: dict) -> list[Assignment]:
        """Accept an event and return the assignments to queue."""
        event_id = envelope.get("event_id")
        event_type = envelope.get("event_type")
        payload = envelope.get("payload") or {}

        if not event_id or not event_type:
            raise MalformedEvent("Envelope without event_id or event_type")

        recipient_ids = payload.get("recipient_ids") or []

        # `source_component`/`source_event_name` (ConversationService) or the generic
        # `component`/`provider`.
        component = payload.get("source_component") or payload.get("component")
        provider = payload.get("source_event_name") or payload.get("provider")

        if not component or not provider:
            raise MalformedEvent(
                f"Event {event_id} does not declare the component and provider of the message: "
                f"without them there is no way to know which channel preference to consult."
            )

        if not recipient_ids:
            logger.info("Event %s without recipients, nothing to deliver", event_id)
            return []

        async with self._session_factory() as session:
            events = SqlEventRepository(session)
            deliveries = SqlDeliveryRepository(session)
            processor_repo = SqlProcessorRepository(session)

            await events.remember(event_id, event_type, envelope)

            enabled_processors = await processor_repo.enabled_names()
            recipients = await self._views.recipients(session, recipient_ids)
            preferences = await self._views.preferences(session, recipient_ids)
            defaults = await self._views.site_defaults(session)
            devices = await self._views.devices(session, recipient_ids)

            triples: list[DeliveryTarget] = []
            for recipient_id in recipient_ids:
                recipient = recipients.get(recipient_id)
                if recipient is None:
                    # The recipient no longer exists.
                    continue
                if not recipient.is_deliverable:
                    continue

                context = {
                    "preferences": preferences.get(recipient_id, {}),
                    "devices": devices.get(recipient_id, []),
                }

                try:
                    channels = resolve_channels(
                        processors=enabled_processors,
                        component=component,
                        provider=provider,
                        recipient=recipient,
                        user_preferences=context["preferences"],
                        site_defaults=defaults,
                        is_configured=self._configured_for(context),
                    )
                except UnknownProvider as error:
                    raise MalformedEvent(str(error)) from error

                triples.extend(
                    DeliveryTarget(
                        event_id=event_id,
                        recipient_id=recipient_id,
                        channel=channel_name,
                        component=component,
                        provider=provider,
                    )
                    for channel_name in channels
                )

            rows = await deliveries.claim(triples)
            await session.commit()

            return [
                Assignment(
                    delivery_id=row.id,
                    event_id=row.event_id,
                    recipient_id=row.recipient_id,
                    channel=row.channel,
                )
                for row in rows
            ]

    def _configured_for(self, context: dict) -> Callable[[str, Recipient], bool]:
        """Close the context over the question the channel resolution asks, so that the resolution stays
        a pure function and the knowledge of what makes a channel usable stays in the processor.
        """

        def configured(name: str, recipient: Recipient) -> bool:
            processor = self._processors.get(name)
            if processor is None:
                return False
            return processor.is_user_configured(recipient, context)

        return configured

    # -- second stage --------------------------------------------------------------------------

    async def perform(self, assignment: Assignment) -> Attempt:
        """Perform a delivery assignment."""
        async with self._session_factory() as session:
            from ..infrastructure.models import STATUS_PENDING, DeliveryRow

            row = await session.get(DeliveryRow, assignment.delivery_id)
            if row is None:
                return Attempt(False, None, "Delivery row missing")
            if row.status != STATUS_PENDING:
                # Already delivered by another replica, or abandoned: nothing to do.
                return Attempt(True, None, f"Gia' in stato {row.status}")

            events = SqlEventRepository(session)
            stored_envelope = await events.envelope_of(row.event_id)
            if stored_envelope is None:
                return await self._abandon(session, row, {}, "Envelope not kept")

            recipients = await self._views.recipients(session, [row.recipient_id])
            recipient = recipients.get(row.recipient_id)
            if recipient is None or not recipient.is_deliverable:
                return await self._abandon(
                    session, row, stored_envelope, "Recipient no longer deliverable"
                )

            processor = self._processors.get(row.channel)
            if processor is None:
                return await self._abandon(session, row, stored_envelope, f"Canale {row.channel} ignoto")

            preferences = await self._views.preferences(session, [row.recipient_id])
            devices = await self._views.devices(session, [row.recipient_id])
            context = {
                "preferences": preferences.get(row.recipient_id, {}),
                "devices": devices.get(row.recipient_id, []),
            }
            content = Content.from_payload(stored_envelope.get("payload") or {})

            attempts = row.attempts + 1

            try:
                await processor.send(recipient, content, context)
            except PermanentFailure as error:
                return await self._abandon(session, row, stored_envelope, str(error), attempts)
            except DeliveryFailure as error:
                if attempts >= self._max_attempts:
                    return await self._abandon(session, row, stored_envelope, str(error), attempts)
                wait_seconds = self._wait(attempts)
                deliveries = SqlDeliveryRepository(session)
                await deliveries.schedule_retry(
                    row.id, attempts, str(error), int(time.time() + wait_seconds)
                )
                await session.commit()
                return Attempt(False, wait_seconds, str(error))

            deliveries = SqlDeliveryRepository(session)
            await deliveries.mark_delivered(row.id, attempts)
            await session.commit()
            return Attempt(True, None, "")

    async def _abandon(
        self,
        session: AsyncSession,
        row,
        stored_envelope: dict,
        why: str,
        attempts: int | None = None,
    ) -> Attempt:
        """Move the delivery to the dead letters and mark its end."""
        count = attempts if attempts is not None else row.attempts
        dead_letters = SqlDeadLetterRepository(session)
        deliveries = SqlDeliveryRepository(session)

        await dead_letters.record(
            event_id=row.event_id,
            recipient_id=row.recipient_id,
            channel=row.channel,
            envelope=stored_envelope,
            reason=why,
            attempts=count,
        )
        await deliveries.mark_dead(row.id, count, why)
        await session.commit()

        logger.warning(
            "Delivery abandoned: event=%s recipient=%s channel=%s reason=%s",
            row.event_id,
            row.recipient_id,
            row.channel,
            why,
        )
        return Attempt(False, None, why)

    def _wait(self, attempts: int) -> float:
        return min(self._retry_base * (2 ** (attempts - 1)), self._retry_max)

    # -- observability -------------------------------------------------------------------------

    async def counts(self) -> dict[str, int]:
        """Counts by status, plus the abandoned ones."""
        async with self._session_factory() as session:
            deliveries = SqlDeliveryRepository(session)
            dead_letters = SqlDeadLetterRepository(session)
            counts = await deliveries.counts_by_status()
            counts["dead_letter"] = await dead_letters.count()
            return counts
