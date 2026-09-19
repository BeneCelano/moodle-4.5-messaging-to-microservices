"""Contacts and the contact rule, the part of the domain that depends on facts owned by
other contexts.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.domain.constants import ConversationType
from app.domain.errors import NotAMember
from app.domain.events import CONTACT_REQUEST_CREATED
from app.infrastructure.models import OutboxRow


async def test_contact_request_event(contacts, session_factory):
    """A contact request records an event and does not notify the recipient directly."""
    request = await contacts.create_request(3, 4)

    assert request.id > 0

    async with session_factory() as session:
        rows = (
            (await session.execute(select(OutboxRow).where(OutboxRow.event_type == CONTACT_REQUEST_CREATED)))
            .scalars()
            .all()
        )

    assert len(rows) == 1
    assert rows[0].envelope["payload"]["requested_user_id"] == 4


async def test_confirm_request(contacts):
    """Confirming turns the request into a contact relationship."""
    await contacts.create_request(3, 4)

    contact = await contacts.confirm_request(3, 4)

    assert contact is not None
    assert await contacts.get_contact(4, 3) is not None
    assert await contacts.requests_between(3, 4) == []


async def test_decline_request(contacts):
    """Declining removes the request without creating the relationship."""
    await contacts.create_request(3, 4)

    assert await contacts.decline_request(3, 4) is True
    assert await contacts.get_contact(3, 4) is None
    assert await contacts.decline_request(3, 4) is False


async def test_blocked_requests_not_counted(contacts):
    await contacts.create_request(3, 10)
    await contacts.create_request(4, 10)
    await contacts.block(10, 4)

    assert await contacts.count_received_requests(10) == 1


async def test_contact_list_symmetry(contacts):
    """The list returns the other user, in either direction."""
    await contacts.add_contact(3, 4)
    await contacts.add_contact(5, 3)

    assert sorted(await contacts.list_contact_ids(3)) == [4, 5]
    assert await contacts.list_contact_ids(4) == [3]


async def test_contact_list_pagination(contacts):
    """Pagination follows the source order: most recent contacts first."""
    for other in (4, 5, 6):
        await contacts.add_contact(3, other)

    first_page = await contacts.list_contact_ids(3, offset=0, limit=2)
    second_page = await contacts.list_contact_ids(3, offset=2, limit=2)

    assert len(first_page) == 2
    assert len(second_page) == 1
    # No id appears in two pages, even when contacts share the creation instant.
    assert set(first_page).isdisjoint(second_page)
    assert sorted(first_page + second_page) == [4, 5, 6]


async def test_block_list_direction(contacts):
    """The block list is directional: who blocks does not appear among their own blocked
    users.
    """
    await contacts.block(3, 4)
    await contacts.block(5, 3)

    assert await contacts.list_blocked_user_ids(3) == [4]
    assert await contacts.list_blocked_user_ids(5) == [3]


async def test_blocked_requests_hidden(contacts):
    """A request from a blocked user stays recorded but does not appear in the list."""
    await contacts.create_request(3, 10)
    await contacts.create_request(4, 10)
    await contacts.block(10, 4)

    assert await contacts.list_received_request_user_ids(10) == [3]
    # The request is not deleted.
    assert await contacts.requests_between(4, 10) != []


async def test_send_by_non_member(conversations, messages):
    """Membership is a necessary condition in every conversation type."""
    group = await conversations.create(int(ConversationType.GROUP), [3, 4], name="Group")

    with pytest.raises(NotAMember):
        await messages.send(int(group.id), 99, "not one of you", 0)
