"""Consumption of the contact request event published by the ConversationService."""

from __future__ import annotations

import json

import pytest

from app.domain.errors import UnknownTemplate
from app.infrastructure.consumer import MalformedEvent

from .conftest import contact_request_envelope, person


class TestNotificationProduced:
    """What the handler writes when the event is valid."""

    async def test_notification_written(self, handler, service, seeded_templates):
        await handler.handle(contact_request_envelope())

        notifications = await service.list_for_user(7)

        assert len(notifications) == 1
        assert notifications[0].user_id_from == 9
        assert notifications[0].component == "moodle"
        assert notifications[0].event_type == "messagecontactrequests"

    async def test_event_type_name(
        self, handler, service, seeded_templates
    ):
        """The type is the key with which delivery looks up the channel preference: a different name would
        produce a notification no channel recognizes.
        """
        await handler.handle(contact_request_envelope())

        notification = (await service.list_for_user(7))[0]
        assert notification.event_type == "messagecontactrequests"

    async def test_recipient_language(
        self, handler, service, views, seeded_templates
    ):
        views.people[7] = person(7, lang="it")

        await handler.handle(contact_request_envelope())

        notification = (await service.list_for_user(7))[0]
        assert "richiesta di contatto" in notification.subject
        assert "Bruno Verdi" in notification.subject
        assert "Test Moodle" in notification.subject

    async def test_language_fallback(
        self, handler, service, views, seeded_templates
    ):
        """A language without template must not produce an empty notification: the real case of language
        packs installed after the seed.
        """
        views.people[7] = person(7, lang="de")

        await handler.handle(contact_request_envelope())

        notification = (await service.list_for_user(7))[0]
        assert "Contact request from" in notification.subject

    async def test_body_two_forms(self, handler, service, seeded_templates):
        """The two fields carry the same thing in two forms."""
        await handler.handle(contact_request_envelope())

        notification = (await service.list_for_user(7))[0]

        assert "<a href=" in notification.full_message_html
        assert "<a href=" not in notification.full_message
        assert "richieste di contatto" in notification.full_message

    async def test_context_url(
        self, handler, service, seeded_templates
    ):
        await handler.handle(contact_request_envelope())

        notification = (await service.list_for_user(7))[0]
        assert notification.context_url == "https://moodle.lab/message/index.php?view=contactrequests"

    async def test_custom_data_labels(
        self, handler, service, seeded_templates
    ):
        await handler.handle(contact_request_envelope())

        notification = (await service.list_for_user(7))[0]
        data = json.loads(notification.custom_data)

        assert data["actionbuttons"] == {"accept": "Accetta", "reject": "Rifiuta"}
        assert data["notificationiconurl"].endswith("/user/pix.php/9/f1.jpg")

    async def test_always_in_popup(self, handler, service, seeded_templates):
        await handler.handle(contact_request_envelope())
        assert await service.count_unread_in_popup(7) == 1

    async def test_event_published(
        self, handler, uow_factory, seeded_templates
    ):
        """The full chain: a consumed event produces another one, through three services none of which
        knows the other two.
        """
        from sqlalchemy import select

        from app.infrastructure.models import OutboxRow

        await handler.handle(contact_request_envelope())

        async with uow_factory() as uow:
            rows = (await uow._session.execute(select(OutboxRow))).scalars().all()

            assert len(rows) == 1
            assert rows[0].event_type == "notification.created"
            assert rows[0].envelope["payload"]["recipient_ids"] == [7]


class TestIdempotency:
    """The same event delivered twice."""

    async def test_duplicate_event(
        self, handler, service, seeded_templates
    ):
        """Broker delivery is at least once, and this is how it does no harm."""
        envelope = contact_request_envelope()

        await handler.handle(envelope)
        await handler.handle(envelope)

        assert len(await service.list_for_user(7)) == 1

    async def test_distinct_events(
        self, handler, service, seeded_templates
    ):
        await handler.handle(contact_request_envelope(event_id="aaa"))
        await handler.handle(contact_request_envelope(event_id="bbb"))

        assert len(await service.list_for_user(7)) == 2


class TestNoNotification:
    """The conditions in which the handler does not write, and it is not a fault."""

    async def test_recipient_deleted(self, handler, service, views, seeded_templates):
        """The race between the contact request and the deletion of the user, won by the
        latter.
        """
        del views.people[7]

        await handler.handle(contact_request_envelope())

        assert await service.list_for_user(7) == []

    async def test_requester_deleted(self, handler, service, views, seeded_templates):
        del views.people[9]

        await handler.handle(contact_request_envelope())

        assert await service.list_for_user(7) == []

    async def test_recipient_opted_out(
        self, handler, service, views, seeded_templates
    ):
        views.people[7] = person(7, email_stop=True)

        await handler.handle(contact_request_envelope())

        assert await service.list_for_user(7) == []

    async def test_other_type_ignored(
        self, handler, service, seeded_templates
    ):
        """The queue is bound to a key pattern and may carry other types: ignoring what is not handled is
        right, rejecting it would discard it for everyone.
        """
        await handler.handle(
            contact_request_envelope(event_type="conversation.conversation.created")
        )

        assert await service.list_for_user(7) == []


class TestRejectedEvents:
    """The conditions in which the handler cannot proceed."""

    async def test_missing_id_malformed(self, handler):
        envelope = contact_request_envelope()
        del envelope["event_id"]

        with pytest.raises(MalformedEvent):
            await handler.handle(envelope)

    async def test_missing_users_malformed(self, handler):
        envelope = contact_request_envelope()
        del envelope["payload"]["requested_user_id"]

        with pytest.raises(MalformedEvent):
            await handler.handle(envelope)

    async def test_missing_templates(self, handler):
        """A service without templates raises instead of writing an empty notification.
        """
        with pytest.raises(UnknownTemplate):
            await handler.handle(contact_request_envelope())
