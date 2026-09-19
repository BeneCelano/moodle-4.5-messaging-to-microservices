"""The operations of the notification context."""

from __future__ import annotations

import pytest

from app.domain.constants import SORT_ASC, SORT_DESC
from app.domain.errors import NotificationNotFound


async def write(service, **kwargs):
    """Write a notification with convenient values, overridable by the test."""
    values = {
        "user_id_from": 9,
        "user_id_to": 7,
        "component": "mod_assign",
        "event_type": "assign_notification",
        "subject": "Assignment due",
        "full_message": "The assignment is due tomorrow",
    }
    values.update(kwargs)
    return await service.send(**values)


class TestWrite:
    """Writing a notification."""

    async def test_send_notification(self, service, clock):
        outcome = await write(service)

        assert outcome["id"] > 0
        assert outcome["time_created"] == clock.now()

        notification = await service.get(outcome["id"])
        assert notification.user_id_to == 7
        assert notification.subject == "Assignment due"
        assert notification.time_read is None
        assert not notification.is_read

    async def test_born_unread(self, service):
        await write(service)
        assert await service.count_unread(7) == 1

    async def test_popup_shown(self, service):
        await write(service, in_popup=True)
        assert await service.count_unread_in_popup(7) == 1
        assert len(await service.list_in_popup(7)) == 1

    async def test_popup_hidden(self, service):
        """The popup is a subset, not the whole list: without its table it would show every notification,
        including those the person chose to receive by mail only.
        """
        await write(service, in_popup=False)

        assert await service.count_unread(7) == 1
        assert await service.count_unread_in_popup(7) == 0
        assert await service.list_in_popup(7) == []

    async def test_idempotent_send(self, service):
        """The monolith repeating the same write must not produce two notifications: the network timeout
        case, where the write happened and the answer was lost.
        """
        first_id = await write(service, idempotency_key="k-1")
        second_id = await write(service, idempotency_key="k-1")

        assert first_id == second_id
        assert await service.count_unread(7) == 1

    async def test_different_keys(self, service):
        await write(service, idempotency_key="k-1")
        await write(service, idempotency_key="k-2")
        assert await service.count_unread(7) == 2

    async def test_send_records_event(
        self, service, uow_factory
    ):
        """The fact and the event announcing it are committed together: the outbox
        guarantee.
        """
        from sqlalchemy import select

        from app.infrastructure.models import OutboxRow

        outcome = await write(service)

        async with uow_factory() as uow:
            rows = (await uow._session.execute(select(OutboxRow))).scalars().all()

            assert len(rows) == 1
            assert rows[0].event_type == "notification.created"
            assert rows[0].published_at is None
            payload = rows[0].envelope["payload"]
            assert payload["notification_id"] == outcome["id"]
            assert payload["recipient_ids"] == [7]

    async def test_event_generic_names(
        self, service, uow_factory
    ):
        """The gateway accepts `component` and `provider` as well as the ConversationService
        names.
        """
        from sqlalchemy import select

        from app.infrastructure.models import OutboxRow

        await write(service, component="mod_quiz", event_type="submission")

        async with uow_factory() as uow:
            row = (await uow._session.execute(select(OutboxRow))).scalars().one()

            payload = row.envelope["payload"]
            assert payload["component"] == "mod_quiz"
            assert payload["provider"] == "submission"


class TestReadState:
    """Marking a notification as read."""

    async def test_mark_read(self, service, clock):
        outcome = await write(service)
        result = await service.mark_read(outcome["id"])

        assert result["changed"] is True
        assert result["time_read"] == clock.now()
        assert await service.count_unread(7) == 0

    async def test_mark_read_twice(self, service, clock):
        """The first reading instant is the only datum that column carries; the popup asks to mark as read
        every time someone reopens something.
        """
        outcome = await write(service)
        first_id = await service.mark_read(outcome["id"])

        clock.advance(3600)
        second_id = await service.mark_read(outcome["id"])

        assert second_id["changed"] is False
        assert second_id["time_read"] == first_id["time_read"]

    async def test_mark_read_missing(self, service):
        with pytest.raises(NotificationNotFound):
            await service.mark_read(999)

    async def test_mark_all_read(self, service):
        await write(service)
        await write(service)
        await write(service, user_id_to=8)

        outcome = await service.mark_all_read(7)

        assert outcome["marked"] == 2
        assert await service.count_unread(7) == 0
        assert await service.count_unread(8) == 1

    async def test_mark_all_read_from_sender(self, service):
        await write(service, user_id_from=9)
        await write(service, user_id_from=10)

        outcome = await service.mark_all_read(7, user_id_from=9)

        assert outcome["marked"] == 1
        assert await service.count_unread(7) == 1

    async def test_mark_all_read_until(self, service, clock):
        """Who opens the popup marks as read what they saw, not what arrived afterwards.
        """
        await write(service)
        boundary = clock.now()

        clock.advance(60)
        await write(service)

        outcome = await service.mark_all_read(7, time_created_to=boundary)

        assert outcome["marked"] == 1
        assert await service.count_unread(7) == 1


class TestReads:
    """The reads on a person's notifications."""

    async def test_list_received(self, service):
        await write(service)
        await write(service, user_id_to=8)

        notifications = await service.list_for_user(7)

        assert len(notifications) == 1
        assert notifications[0].user_id_to == 7

    async def test_filter_by_read_state(self, service):
        first = await write(service)
        await write(service)
        await service.mark_read(first["id"])

        assert len(await service.list_for_user(7, read=True)) == 1
        assert len(await service.list_for_user(7, read=False)) == 1
        assert len(await service.list_for_user(7)) == 2

    async def test_default_order(self, service, clock):
        first = await write(service)
        clock.advance(60)
        second = await write(service)

        descending = await service.list_for_user(7, sort=SORT_DESC)
        ascending = await service.list_for_user(7, sort=SORT_ASC)

        assert [n.id for n in descending] == [second["id"], first["id"]]
        assert [n.id for n in ascending] == [first["id"], second["id"]]

    async def test_deterministic_order(self, service):
        """Several notifications born from the same action share the instant: without the two tie-breaking
        columns the order would depend on the engine.
        """
        ids = [(await write(service))["id"] for _ in range(5)]

        read_ones = [n.id for n in await service.list_for_user(7, sort=SORT_ASC)]

        assert read_ones == sorted(ids)

    async def test_window(self, service, clock):
        for _ in range(5):
            await write(service)
            clock.advance(1)

        assert len(await service.list_for_user(7, limit_num=2)) == 2
        assert len(await service.list_for_user(7, limit_from=3)) == 2
        assert len(await service.list_for_user(7, limit_from=1, limit_num=2)) == 2

    async def test_count_unread_from_sender(self, service):
        await write(service, user_id_from=9)
        await write(service, user_id_from=10)

        assert await service.count_unread(7) == 2
        assert await service.count_unread(7, user_id_from=9) == 1


class TestPopup:
    """The reads of the notification popup."""

    async def test_popup_own_only(self, service):
        await write(service, user_id_to=7)
        await write(service, user_id_to=8)

        assert len(await service.list_in_popup(7)) == 1

    async def test_popup_unread_count(self, service):
        first = await write(service)
        await write(service)
        await service.mark_read(first["id"])

        assert await service.count_unread_in_popup(7) == 1
        assert len(await service.list_in_popup(7)) == 2


class TestModuleDeduplication:
    """The question `mod_assign` and `mod_quiz` asked the table directly."""

    async def test_already_sent(self, service):
        await write(
            service,
            component="mod_assign",
            event_type="assign_due_digest",
            custom_data='{"assignmentid":12,"duedate":1760000000}',
        )

        assert await service.exists_with_custom_data(
            7,
            '{"assignmentid":12,"duedate":1760000000}',
            component="mod_assign",
            event_type="assign_due_digest",
        )

    async def test_distinct_due_dates(self, service):
        await write(service, custom_data='{"assignmentid":12}')

        assert not await service.exists_with_custom_data(7, '{"assignmentid":13}')

    async def test_distinct_people(self, service):
        await write(service, user_id_to=7, custom_data='{"assignmentid":12}')

        assert not await service.exists_with_custom_data(8, '{"assignmentid":12}')

    async def test_component_filter(self, service):
        await write(service, component="mod_quiz", custom_data='{"quizid":3}')

        assert await service.exists_with_custom_data(7, '{"quizid":3}')
        assert not await service.exists_with_custom_data(7, '{"quizid":3}', component="mod_assign")

    async def test_compare_255_chars(self, service):
        """The form of the comparison the monolith performs (`sql_compare_text`, 255 characters), not an
        approximation chosen here.
        """
        long_text = "x" * 300
        await write(service, custom_data=long_text)

        assert await service.exists_with_custom_data(7, "x" * 255 + "y" * 45)


class TestCleanup:
    """The periodic cleanup."""

    async def test_delete_created_before(self, service, clock):
        await write(service)
        clock.advance(1000)
        recent = await write(service)

        count = await service.delete_created_before(clock.now() - 500)

        assert count == 1
        remaining = await service.list_for_user(7)
        assert [n.id for n in remaining] == [recent["id"]]

    async def test_delete_read_before(self, service, clock):
        read_one = await write(service)
        await write(service)
        await service.mark_read(read_one["id"])

        clock.advance(1000)
        count = await service.delete_read_before(clock.now())

        assert count == 1
        assert await service.count_unread(7) == 1

    async def test_cleanup_cascades(
        self, service, uow_factory, clock
    ):
        """The cascade replaces the monolith's two separate cleanup paths, which must
        stay in step.
        """
        from sqlalchemy import func, select

        from app.infrastructure.models import PopupNotificationRow

        await write(service, in_popup=True)
        clock.advance(1000)

        await service.delete_created_before(clock.now())

        async with uow_factory() as uow:
            remaining = await uow._session.execute(select(func.count(PopupNotificationRow.id)))
            assert int(remaining.scalar() or 0) == 0


class TestRemoval:
    """The removal of a single notification, needed by the erasure of personal data and by the reroute
    verification.
    """

    async def test_delete_notification(self, service):
        outcome = await write(service)

        assert await service.delete(outcome["id"]) is True
        assert await service.list_for_user(7) == []

    async def test_delete_missing(self, service):
        """Declares a final state, the notification is gone, reached in both cases."""
        assert await service.delete(999) is False

    async def test_delete_cascades(self, service, uow_factory):
        """In the monolith there is no foreign key between the two tables: who deletes must remember the
        other row.
        """
        from sqlalchemy import func, select

        from app.infrastructure.models import PopupNotificationRow

        outcome = await write(service, in_popup=True)
        await service.delete(outcome["id"])

        async with uow_factory() as uow:
            remaining = await uow._session.execute(select(func.count(PopupNotificationRow.id)))
            assert int(remaining.scalar() or 0) == 0


class TestPersonalData:
    """The three operations the law imposes."""

    async def test_recipient_has_data(self, service):
        await write(service, user_id_to=7)

        assert await service.has_personal_data(7) is True
        assert await service.has_personal_data(99) is False

    async def test_sender_has_data(self, service):
        """The sender side is what an ordinary read never looks at; the monolith does
        (`useridfrom = ? OR useridto = ?`), since the sender's name is personal data too.
        """
        await write(service, user_id_from=9, user_id_to=7)

        assert await service.has_personal_data(9) is True

    async def test_export_both_directions(self, service, clock):
        received = await write(service, user_id_from=9, user_id_to=7)
        clock.advance(10)
        sent = await write(service, user_id_from=7, user_id_to=8)
        clock.advance(10)
        await write(service, user_id_from=9, user_id_to=8)

        exported = await service.export_personal_data(7)

        assert [n.id for n in exported] == [received["id"], sent["id"]]

    async def test_export_order(self, service, clock):
        """The order is the monolith's, `timecreated ASC`: an export is read from the
        beginning.
        """
        first = await write(service)
        clock.advance(60)
        second = await write(service)

        exported = await service.export_personal_data(7)

        assert [n.id for n in exported] == [first["id"], second["id"]]

    async def test_erase_both_directions(self, service):
        await write(service, user_id_from=9, user_id_to=7)
        await write(service, user_id_from=7, user_id_to=8)
        unrelated = await write(service, user_id_from=9, user_id_to=8)

        count = await service.erase_personal_data(7)

        assert count == 2
        assert await service.has_personal_data(7) is False
        assert (await service.get(unrelated["id"])).id == unrelated["id"]

    async def test_erase_nothing(self, service):
        assert await service.erase_personal_data(99) == 0

    async def test_erase_cascades(self, service, uow_factory):
        from sqlalchemy import func, select

        from app.infrastructure.models import PopupNotificationRow

        await write(service, in_popup=True)
        await write(service, in_popup=True)

        await service.erase_personal_data(7)

        async with uow_factory() as uow:
            remaining = await uow._session.execute(select(func.count(PopupNotificationRow.id)))
            assert int(remaining.scalar() or 0) == 0
