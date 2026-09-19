"""The list of a user's conversations: the most composite read model of the service."""

from __future__ import annotations

from app.domain.constants import ConversationType


async def test_list_order(conversations, messages):
    """Conversations come most recent first, and those without messages go last."""
    first = await conversations.create(int(ConversationType.INDIVIDUAL), [3, 4])
    second = await conversations.create(int(ConversationType.INDIVIDUAL), [3, 5])
    silent = await conversations.create(int(ConversationType.GROUP), [3, 6], name="Silence")

    await messages.send(conversation_id=int(first.id), user_id_from=4, text="first", message_format=0)
    await messages.send(conversation_id=int(second.id), user_id_from=5, text="then", message_format=0)

    entries = await messages.list_conversations(3)
    ids = [entry.id for entry in entries]

    assert ids.index(int(second.id)) < ids.index(int(first.id))
    assert ids[-1] == int(silent.id), "A conversation without messages cannot precede one that has some"


async def test_last_message_visibility(conversations, messages, clock):
    """The last message depends on who is looking: deletion is per user."""
    conversation = await conversations.create(int(ConversationType.INDIVIDUAL), [3, 4])
    await messages.send(conversation_id=int(conversation.id), user_id_from=4, text="old", message_format=0)
    clock.advance(60)
    recent = await messages.send(
        conversation_id=int(conversation.id), user_id_from=4, text="recent", message_format=0
    )

    await messages.delete_for_user(recent["id"], user_id=3)

    for_deleter = await messages.list_conversations(3)
    for_other = await messages.list_conversations(4)

    assert for_deleter[0].last_message.small_message == "old"
    assert for_other[0].last_message.small_message == "recent"


async def test_same_instant_messages(conversations, messages):
    """Documents a behaviour inherited from the source, not a choice of this service."""
    conversation = await conversations.create(int(ConversationType.INDIVIDUAL), [3, 4])
    await messages.send(conversation_id=int(conversation.id), user_id_from=4, text="old", message_format=0)
    recent = await messages.send(
        conversation_id=int(conversation.id), user_id_from=4, text="recent", message_format=0
    )

    await messages.delete_for_user(recent["id"], user_id=3)

    entries = await messages.list_conversations(3)

    assert entries[0].last_message.small_message == "recent"


async def test_members_shown(conversations, messages):
    """Individual: the other end."""
    individual = await conversations.create(int(ConversationType.INDIVIDUAL), [3, 4])
    group = await conversations.create(int(ConversationType.GROUP), [3, 4, 5], name="Course")
    self_conv = await conversations.create(int(ConversationType.SELF), [3])

    # The last message is the user's own: the other end is looked up among the members.
    await messages.send(conversation_id=int(individual.id), user_id_from=3, text="me", message_format=0)
    await messages.send(conversation_id=int(group.id), user_id_from=5, text="us", message_format=0)
    await messages.send(conversation_id=int(self_conv.id), user_id_from=3, text="reminder", message_format=0)

    by_id = {entry.id: entry for entry in await messages.list_conversations(3)}

    assert by_id[int(individual.id)].member_ids == [4]
    assert by_id[int(group.id)].member_ids == [5]
    assert by_id[int(self_conv.id)].member_ids == [3]


async def test_empty_group_listed(conversations, messages):
    """A group without messages appears in the list with nobody to show."""
    group = await conversations.create(int(ConversationType.GROUP), [3, 4], name="Course")

    entries = await messages.list_conversations(3)

    assert len(entries) == 1
    assert entries[0].id == int(group.id)
    assert entries[0].member_ids == []
    assert entries[0].last_message is None


async def test_list_counts(conversations, messages):
    """Each entry carries its member count and its unread count."""
    group = await conversations.create(int(ConversationType.GROUP), [3, 4, 5], name="Course")

    await messages.send(conversation_id=int(group.id), user_id_from=4, text="one", message_format=0)
    await messages.send(conversation_id=int(group.id), user_id_from=5, text="two", message_format=0)
    await messages.send(conversation_id=int(group.id), user_id_from=3, text="mine", message_format=0)

    entry = (await messages.list_conversations(3))[0]

    assert entry.member_count == 3
    assert entry.unread_count == 2, "Messages sent by the reader are not their unread messages"


async def test_emptied_individual_hidden(conversations, messages):
    """With every visible message deleted, the individual conversation disappears only
    for the deleter.
    """
    conversation = await conversations.create(int(ConversationType.INDIVIDUAL), [3, 4])
    sent = await messages.send(
        conversation_id=int(conversation.id), user_id_from=4, text="Hello", message_format=0
    )

    await messages.delete_for_user(sent["id"], user_id=3)

    assert await messages.list_conversations(3) == []
    assert len(await messages.list_conversations(4)) == 1


async def test_self_conversation_visibility(conversations, messages):
    """The just-created self conversation appears; the one emptied by deletions does
    not.
    """
    self_conv = await conversations.create(int(ConversationType.SELF), [3])

    assert len(await messages.list_conversations(3)) == 1

    sent = await messages.send(
        conversation_id=int(self_conv.id), user_id_from=3, text="Reminder", message_format=0
    )
    await messages.delete_for_user(sent["id"], user_id=3)

    assert await messages.list_conversations(3) == []


async def test_favourite_filter(conversations, messages):
    """The service does not know favourites: it receives them and uses them inside the
    query.
    """
    favourite = await conversations.create(int(ConversationType.INDIVIDUAL), [3, 4])
    plain = await conversations.create(int(ConversationType.INDIVIDUAL), [3, 5])

    await messages.send(conversation_id=int(favourite.id), user_id_from=4, text="a", message_format=0)
    await messages.send(conversation_id=int(plain.id), user_id_from=5, text="b", message_format=0)

    only = await messages.list_conversations(3, favourite_ids=[int(favourite.id)], only_favourites=True)
    excluded = await messages.list_conversations(3, favourite_ids=[int(favourite.id)], only_favourites=False)
    all_entries = await messages.list_conversations(3, favourite_ids=[int(favourite.id)])

    assert [entry.id for entry in only] == [int(favourite.id)]
    assert [entry.id for entry in excluded] == [int(plain.id)]
    assert len(all_entries) == 2, "Without the direction of the filter, the ids received filter nothing"


async def test_type_filter_merge_self(conversations, messages):
    """Self conversations are a type of their own in the schema, but one entry in the
    interface.
    """
    individual = await conversations.create(int(ConversationType.INDIVIDUAL), [3, 4])
    self_conv = await conversations.create(int(ConversationType.SELF), [3])
    await conversations.create(int(ConversationType.GROUP), [3, 5], name="Course")

    await messages.send(conversation_id=int(individual.id), user_id_from=4, text="a", message_format=0)

    separate = await messages.list_conversations(3, conversation_type=int(ConversationType.INDIVIDUAL))
    merged = await messages.list_conversations(
        3, conversation_type=int(ConversationType.INDIVIDUAL), merge_self=True
    )

    assert [entry.id for entry in separate] == [int(individual.id)]
    assert {entry.id for entry in merged} == {int(individual.id), int(self_conv.id)}


async def test_mute_flag(conversations, messages):
    """Muting is per user: the same conversation is muted for one and not for the other.
    """
    conversation = await conversations.create(int(ConversationType.GROUP), [3, 4], name="Course")

    await conversations.mute(int(conversation.id), user_id=3)

    for_muter = (await messages.list_conversations(3))[0]
    for_other = (await messages.list_conversations(4))[0]

    assert for_muter.is_muted is True
    assert for_other.is_muted is False


async def test_list_pagination(conversations, messages):
    """Successive pages neither repeat nor skip conversations."""
    created = []
    for other in (4, 5, 6):
        conversation = await conversations.create(int(ConversationType.INDIVIDUAL), [3, other])
        await messages.send(
            conversation_id=int(conversation.id), user_id_from=other, text="x", message_format=0
        )
        created.append(int(conversation.id))

    first = await messages.list_conversations(3, offset=0, limit=2)
    second = await messages.list_conversations(3, offset=2, limit=2)

    assert len(first) == 2
    assert len(second) == 1
    assert {entry.id for entry in first}.isdisjoint({entry.id for entry in second})
    assert {entry.id for entry in first} | {entry.id for entry in second} == set(created)
