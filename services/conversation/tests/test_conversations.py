"""Invariants of the conversation aggregate."""

from __future__ import annotations

import pytest

from app.domain.constants import ConversationState, ConversationType
from app.domain.errors import ConversationNotFound, InvalidConversation


async def test_unique_conversation(conversations):
    """Asking twice for the same individual conversation does not create a second one.
    """
    first = await conversations.create(int(ConversationType.INDIVIDUAL), [3, 4])
    second = await conversations.create(int(ConversationType.INDIVIDUAL), [4, 3])

    assert first.id == second.id


async def test_max_two_members(conversations):
    """An individual conversation cannot have more than two participants."""
    with pytest.raises(InvalidConversation):
        await conversations.create(int(ConversationType.INDIVIDUAL), [3, 4, 5])


async def test_self_single_member(conversations):
    """A self conversation has one participant."""
    with pytest.raises(InvalidConversation):
        await conversations.create(int(ConversationType.SELF), [3, 4])

    conversation = await conversations.create(int(ConversationType.SELF), [3])
    assert conversation.member_ids() == [3]


async def test_add_members_only_groups(conversations):
    individual = await conversations.create(int(ConversationType.INDIVIDUAL), [3, 4])

    with pytest.raises(InvalidConversation):
        await conversations.add_members(int(individual.id), [5])

    group = await conversations.create(int(ConversationType.GROUP), [3, 4], name="Group")
    assert await conversations.add_members(int(group.id), [5, 6]) == 2
    assert await conversations.count_members(int(group.id)) == 4


async def test_duplicate_member_ignored(conversations):
    """Adding a member already present does not duplicate it."""
    group = await conversations.create(int(ConversationType.GROUP), [3, 4], name="Group")

    assert await conversations.add_members(int(group.id), [4, 5]) == 1
    assert sorted(await conversations.member_ids(int(group.id))) == [3, 4, 5]


async def test_disable_and_rename(conversations):
    """The conversation linked to a group follows the group life cycle."""
    group = await conversations.create(
        int(ConversationType.GROUP),
        [3, 4],
        name="Old name",
        component="core_group",
        item_type="groups",
        item_id=7,
        context_id=11,
    )

    updated = await conversations.update(int(group.id), enabled=int(ConversationState.DISABLED))
    assert updated.is_enabled is False

    renamed = await conversations.update(int(group.id), name="New name")
    assert renamed.name == "New name"
    assert renamed.is_enabled is False, "Renaming does not touch the enabled state"


async def test_lookup_by_area(conversations):
    """The conversation linked to an item can be found from it."""
    await conversations.create(
        int(ConversationType.GROUP),
        [3, 4],
        name="Group",
        component="core_group",
        item_type="groups",
        item_id=7,
        context_id=11,
    )

    found = await conversations.get_by_area("core_group", "groups", 7, 11)
    assert found.item_id == 7

    with pytest.raises(ConversationNotFound):
        await conversations.get_by_area("core_group", "groups", 8, 11)


async def test_delete_conversation(conversations, messages):
    """Removing a conversation removes the data owned by this context."""
    conversation = await conversations.create(int(ConversationType.GROUP), [3, 4], name="Group")
    await messages.send(
        conversation_id=int(conversation.id), user_id_from=3, text="Hello", message_format=0
    )

    await conversations.delete_all_data(int(conversation.id))

    with pytest.raises(ConversationNotFound):
        await conversations.get(int(conversation.id))
    assert await messages.list_conversation_messages(3, int(conversation.id)) == []


async def test_mute_per_user(conversations):
    """Muting is per user, not per conversation."""
    conversation = await conversations.create(int(ConversationType.GROUP), [3, 4], name="Group")

    await conversations.mute(int(conversation.id), 3)

    assert await conversations.is_muted(int(conversation.id), 3) is True
    assert await conversations.is_muted(int(conversation.id), 4) is False

    await conversations.unmute(int(conversation.id), 3)
    assert await conversations.is_muted(int(conversation.id), 3) is False


async def test_duplicate_hash(conversations, session_factory):
    """Two conversations with the same hash do not break the lookup: the oldest wins."""
    from sqlalchemy import select

    from app.infrastructure.models import ConversationRow

    original = await conversations.create(int(ConversationType.INDIVIDUAL), [3, 4])

    # The twin is inserted directly, as the race between two requests would produce it.
    async with session_factory() as session:
        row = (
            await session.execute(
                select(ConversationRow).where(ConversationRow.id == original.id)
            )
        ).scalar_one()

        session.add(
            ConversationRow(
                type=row.type,
                conversation_hash=row.conversation_hash,
                enabled=row.enabled,
                time_created=row.time_created + 1,
            )
        )
        await session.commit()

    found = await conversations.get_individual([3, 4])

    assert found.id == original.id
