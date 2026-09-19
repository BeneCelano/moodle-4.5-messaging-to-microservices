"""The two stages of delivery."""

from __future__ import annotations

import pytest
from sqlalchemy import text

from app.application.deliver import Assignment, MalformedEvent

from .conftest import (
    DeliveryFailure,
    FakeProcessor,
    PermanentFailure,
    device,
    envelope,
    recipient,
)


async def rows(session_factory, table: str, event_id: str = "event-1") -> list:
    """The rows of a table for an event, read without the repositories."""
    order = "" if table == "event" else " ORDER BY channel"
    async with session_factory() as session:
        result = await session.execute(
            text(f"SELECT * FROM {table} WHERE event_id = :e{order}"),
            {"e": event_id},
        )
        return list(result.mappings())


# ---------------------------------------------------------------------------------------------------
# First stage: acceptance
# ---------------------------------------------------------------------------------------------------


async def test_missing_id_malformed(gateway):
    with pytest.raises(MalformedEvent):
        await gateway.accept({"event_type": "conversation.message.sent", "payload": {}})


async def test_missing_provider_malformed(gateway):
    """Without component and provider there is no way to know which preference to consult: an event that
    cannot be read, not a failed delivery.
    """
    envelope_dict = envelope()
    del envelope_dict["payload"]["source_component"]
    del envelope_dict["payload"]["source_event_name"]

    with pytest.raises(MalformedEvent):
        await gateway.accept(envelope_dict)


async def test_generic_field_names(gateway, session_factory):
    """The contract accepts `component`/`provider` as well as the ConversationService
    names.
    """
    envelope_dict = envelope()
    component = envelope_dict["payload"].pop("source_component")
    provider = envelope_dict["payload"].pop("source_event_name")
    envelope_dict["payload"]["component"] = component
    envelope_dict["payload"]["provider"] = provider

    assignments = await gateway.accept(envelope_dict)

    assert [i.channel for i in assignments] == ["email"]


async def test_no_recipients(gateway, session_factory):
    """Not an error: an event that concerns nobody."""
    assignments = await gateway.accept(envelope(recipient_ids=()))

    assert assignments == []
    assert await rows(session_factory, "delivery") == []


async def test_assignment_per_channel(gateway, session_factory, views):
    """With a registered device the recipient receives on both channels."""
    views.device_map = {10: [device(10)]}

    assignments = await gateway.accept(envelope())

    assert sorted(i.channel for i in assignments) == ["airnotifier", "email"]
    assert len(await rows(session_factory, "delivery")) == 2


async def test_push_needs_device(gateway, session_factory):
    """Moodle's rule: airnotifier delivers to the devices, not to the user."""
    assignments = await gateway.accept(envelope())

    assert [i.channel for i in assignments] == ["email"]


async def test_envelope_kept(gateway, session_factory):
    """Needed by the second stage, which recomposes the content without asking anyone.
    """
    await gateway.accept(envelope())

    kept = await rows(session_factory, "event")
    assert len(kept) == 1
    assert kept[0]["event_type"] == "conversation.message.sent"


async def test_duplicate_event(gateway, session_factory):
    """The broker may redeliver: the idempotent receiver must not produce a second send.
    """
    first_ones = await gateway.accept(envelope())
    second_ones = await gateway.accept(envelope())

    assert len(await rows(session_factory, "delivery")) == len(first_ones)
    assert [i.delivery_id for i in second_ones] == [i.delivery_id for i in first_ones]


async def test_unknown_recipient_skipped(gateway, session_factory, views):
    """The race between the message and the deletion of the user, won by the latter: the vanished
    recipient is skipped silently, the others must still receive.
    """
    views.people = {10: recipient(10), 11: recipient(11)}

    assignments = await gateway.accept(envelope(recipient_ids=(10, 11, 999)))

    assert sorted(i.recipient_id for i in assignments) == [10, 11]


async def test_suspended_recipient(gateway, views):
    """The state is read at delivery time, not when the message was written."""
    views.people = {10: recipient(10, suspended=1)}

    assert await gateway.accept(envelope()) == []


async def test_nologin_recipient(gateway, views):
    """`auth = nologin` is how Moodle disables a user without deleting it."""
    views.people = {10: recipient(10, auth_method="nologin")}

    assert await gateway.accept(envelope()) == []


async def test_channel_without_processor(gateway, views, processors):
    """What the gateway does not implement must not even be assigned: the case of `popup`, enabled on the
    site but not registered by the gateway.
    """
    views.device_map = {10: [device(10)]}
    processors["airnotifier"].configured = False

    assignments = await gateway.accept(envelope())

    assert [i.channel for i in assignments] == ["email"]


# ---------------------------------------------------------------------------------------------------
# Second stage: execution
# ---------------------------------------------------------------------------------------------------


async def test_success_records_attempt(
    gateway, session_factory, processors
):
    """The attempt count is written on success too."""
    [assignment] = await gateway.accept(envelope())

    outcome = await gateway.perform(assignment)

    assert outcome.delivered
    assert processors["email"].sent == [(10, processors["email"].sent[0][1])]
    [row] = await rows(session_factory, "delivery")
    assert row["status"] == "delivered"
    assert row["attempts"] == 1


async def test_content_from_event(gateway, processors):
    """The gateway does not compose: it picks among the forms present in the envelope.
    """
    [assignment] = await gateway.accept(envelope(payload={"text": "buongiorno"}))

    await gateway.perform(assignment)

    _, content = processors["email"].sent[0]
    assert content.full_message == "buongiorno"
    # For a user message the monolith composes no subject.
    assert content.subject == ""


async def test_temporary_failure_retried(gateway, session_factory, processors):
    """The status stays `pending`: there is no "failed but retryable"."""
    processors["email"].fault = DeliveryFailure("the server does not answer")
    [assignment] = await gateway.accept(envelope())

    outcome = await gateway.perform(assignment)

    assert not outcome.delivered
    assert outcome.retry_after_seconds is not None
    [row] = await rows(session_factory, "delivery")
    assert row["status"] == "pending"
    assert row["attempts"] == 1
    assert "does not answer" in row["last_error"]


async def test_exhausted_attempts_dead_letter(gateway, session_factory, processors):
    """The phase criterion: what the monolith loses in silence stays here."""
    processors["email"].fault = DeliveryFailure("HTTP 503")
    [assignment] = await gateway.accept(envelope())

    for _ in range(3):
        await gateway.perform(assignment)

    [row] = await rows(session_factory, "delivery")
    assert row["status"] == "dead"
    assert row["attempts"] == 3

    [dead_row] = await rows(session_factory, "dead_letter")
    assert dead_row["attempts"] == 3
    assert "503" in dead_row["reason"]
    assert dead_row["envelope"], "the envelope must be kept whole"


async def test_permanent_failure_not_retried(gateway, session_factory, processors):
    """An address refused by the server stays refused: retrying would produce the same
    outcome.
    """
    processors["email"].fault = PermanentFailure("missing recipient")
    [assignment] = await gateway.accept(envelope())

    outcome = await gateway.perform(assignment)

    assert not outcome.delivered
    assert outcome.retry_after_seconds is None
    [row] = await rows(session_factory, "delivery")
    assert row["status"] == "dead"
    assert row["attempts"] == 1
    assert len(await rows(session_factory, "dead_letter")) == 1


async def test_delivered_not_repeated(gateway, processors):
    """Two replicas may receive the same assignment: the second must not deliver again.
    """
    [assignment] = await gateway.accept(envelope())
    await gateway.perform(assignment)

    outcome = await gateway.perform(assignment)

    assert outcome.delivered, "the repetition is harmless, not a failure"
    assert len(processors["email"].sent) == 1


async def test_recipient_gone_dead_letter(
    gateway, session_factory, views, processors
):
    """Between acceptance and execution there is a queue, and in that time a user may
    disappear.
    """
    [assignment] = await gateway.accept(envelope())
    views.people = {}

    outcome = await gateway.perform(assignment)

    assert not outcome.delivered
    assert processors["email"].sent == []
    [dead_row] = await rows(session_factory, "dead_letter")
    assert "deliverable" in dead_row["reason"]


async def test_unknown_channel_dead_letter(
    gateway, session_factory
):
    """It may happen if a channel is removed while its assignments are still queued."""
    await gateway.accept(envelope())
    [row] = await rows(session_factory, "delivery")
    made_up = Assignment(
        delivery_id=row["id"], event_id="event-1", recipient_id=10, channel="email"
    )
    gateway._processors = {}

    outcome = await gateway.perform(made_up)

    assert not outcome.delivered
    [dead_row] = await rows(session_factory, "dead_letter")
    assert "ignoto" in dead_row["reason"]


async def test_missing_assignment(gateway):
    """A message left in the queue after a restore names a row that no longer exists."""
    outcome = await gateway.perform(
        Assignment(delivery_id=99999, event_id="event-1", recipient_id=10, channel="email")
    )

    assert not outcome.delivered
    assert "missing" in outcome.reason


async def test_channels_independent(
    gateway, session_factory, views, processors
):
    """The two channels are distinct assignments: the failure of one is not the failure of the other, the
    reason the first stage emits one row per channel.
    """
    views.device_map = {10: [device(10)]}
    processors["airnotifier"] = FakeProcessor(
        "airnotifier", fault=DeliveryFailure("503"), requires_devices=True
    )
    gateway._processors = processors

    assignments = await gateway.accept(envelope())
    for assignment in assignments:
        await gateway.perform(assignment)

    status = {r["channel"]: r["status"] for r in await rows(session_factory, "delivery")}
    assert status == {"airnotifier": "pending", "email": "delivered"}
    assert processors["email"].sent != []
