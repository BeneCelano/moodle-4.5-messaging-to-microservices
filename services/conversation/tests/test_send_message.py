"""The sending path, the object of the experimental comparison."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.domain.constants import ConversationType
from app.domain.errors import NotAMember
from app.domain.events import MESSAGE_SENT
from app.infrastructure.models import OutboxRow


async def test_send_message(conversations, messages, session_factory):
    """Sending persists the message and records the event in the same transaction."""
    conversation = await conversations.create(int(ConversationType.INDIVIDUAL), [3, 4])

    result = await messages.send(
        conversation_id=int(conversation.id),
        user_id_from=3,
        text="Hello",
        message_format=0,
    )

    assert result["id"] > 0
    assert result["text"] == "Hello"

    async with session_factory() as session:
        stored = await session.execute(select(OutboxRow).where(OutboxRow.event_type == MESSAGE_SENT))
        rows = stored.scalars().all()

    assert len(rows) == 1
    envelope = rows[0].envelope
    assert envelope["payload"]["message_id"] == result["id"]
    assert envelope["payload"]["recipient_ids"] == [4]
    assert rows[0].published_at is None, "The event is delivered by the relay, not by the request"


async def test_send_by_non_member(conversations, messages):
    """Who is not a member of the conversation cannot write to it."""
    conversation = await conversations.create(int(ConversationType.INDIVIDUAL), [3, 4])

    with pytest.raises(NotAMember):
        await messages.send(
            conversation_id=int(conversation.id),
            user_id_from=99,
            text="Hello",
            message_format=0,
        )


async def test_idempotent_send(conversations, messages, session_factory):
    """A repeated attempt with the same key returns the message already created."""
    conversation = await conversations.create(int(ConversationType.INDIVIDUAL), [3, 4])

    first = await messages.send(
        conversation_id=int(conversation.id),
        user_id_from=3,
        text="Hello",
        message_format=0,
        idempotency_key="command-1",
    )
    second = await messages.send(
        conversation_id=int(conversation.id),
        user_id_from=3,
        text="Hello",
        message_format=0,
        idempotency_key="command-1",
    )

    assert first["id"] == second["id"]

    async with session_factory() as session:
        events = (await session.execute(select(OutboxRow))).scalars().all()

    published_message_events = [row for row in events if row.event_type == MESSAGE_SENT]
    assert len(published_message_events) == 1


async def test_mark_as_read(conversations, messages):
    """Reading returns the messages with the instant the reader saw them."""
    conversation = await conversations.create(int(ConversationType.INDIVIDUAL), [3, 4])
    sent = await messages.send(
        conversation_id=int(conversation.id), user_id_from=3, text="Hello", message_format=0
    )

    before = await messages.list_conversation_messages(user_id=4, conversation_id=int(conversation.id))
    assert before[0]["time_read"] is None

    await messages.mark_as_read(sent["id"], user_id=4)

    after = await messages.list_conversation_messages(user_id=4, conversation_id=int(conversation.id))
    assert after[0]["time_read"] is not None


async def test_delete_for_user(conversations, messages):
    """Deletion is per user: the other participants still see the message."""
    conversation = await conversations.create(int(ConversationType.INDIVIDUAL), [3, 4])
    sent = await messages.send(
        conversation_id=int(conversation.id), user_id_from=3, text="Hello", message_format=0
    )

    await messages.delete_for_user(sent["id"], user_id=3)

    for_sender = await messages.list_conversation_messages(user_id=3, conversation_id=int(conversation.id))
    for_recipient = await messages.list_conversation_messages(user_id=4, conversation_id=int(conversation.id))

    assert for_sender == []
    assert len(for_recipient) == 1


async def test_count_unread_conversations(conversations, messages):
    first = await conversations.create(int(ConversationType.INDIVIDUAL), [3, 4])
    second = await conversations.create(int(ConversationType.INDIVIDUAL), [3, 5])

    await messages.send(
        conversation_id=int(first.id), user_id_from=3, text="one", message_format=0
    )
    await messages.send(
        conversation_id=int(first.id), user_id_from=3, text="two", message_format=0
    )
    await messages.send(
        conversation_id=int(second.id), user_id_from=3, text="three", message_format=0
    )

    assert await messages.count_unread_conversations(4) == 1
    assert await messages.count_unread_conversations(3) == 0


async def test_list_unread_conversations(conversations, messages):
    individual = await conversations.create(int(ConversationType.INDIVIDUAL), [3, 4])
    group = await conversations.create(int(ConversationType.GROUP), [3, 4, 5], name="Course")

    await messages.send(
        conversation_id=int(individual.id), user_id_from=3, text="one", message_format=0
    )
    await messages.send(
        conversation_id=int(individual.id), user_id_from=3, text="two", message_format=0
    )
    await messages.send(
        conversation_id=int(group.id), user_id_from=3, text="three", message_format=0
    )

    entries = dict(await messages.list_unread_conversations(4))

    assert entries == {
        int(individual.id): int(ConversationType.INDIVIDUAL),
        int(group.id): int(ConversationType.GROUP),
    }
    assert await messages.list_unread_conversations(3) == []


async def test_deleted_message_not_unread(conversations, messages):
    """In the list a deleted message does not count, while in the plain count it still does (as in
    the monolith).
    """
    conversation = await conversations.create(int(ConversationType.INDIVIDUAL), [3, 4])
    sent = await messages.send(
        conversation_id=int(conversation.id), user_id_from=3, text="Hello", message_format=0
    )

    await messages.delete_for_user(sent["id"], user_id=4)

    assert await messages.list_unread_conversations(4) == []
    assert await messages.count_unread_conversations(4) == 1


async def test_countable_by_type(conversations, messages):
    """A group without messages counts, an individual conversation without visible
    messages does not.
    """
    individual = await conversations.create(int(ConversationType.INDIVIDUAL), [3, 4])
    group = await conversations.create(int(ConversationType.GROUP), [3, 4], name="Course")
    silent = await conversations.create(int(ConversationType.INDIVIDUAL), [3, 5])

    await messages.send(
        conversation_id=int(individual.id), user_id_from=4, text="Hello", message_format=0
    )

    entries = dict(await messages.list_countable_conversations(3))

    assert int(group.id) in entries, "A group exists as a group, not as an exchange"
    assert int(individual.id) in entries
    assert int(silent.id) not in entries, "An individual conversation without visible messages does not count"


async def test_emptied_not_countable(conversations, messages):
    """Deleting the last visible message removes the conversation from the count, only
    for who deleted it.
    """
    conversation = await conversations.create(int(ConversationType.INDIVIDUAL), [3, 4])
    sent = await messages.send(
        conversation_id=int(conversation.id), user_id_from=4, text="Hello", message_format=0
    )

    assert int(conversation.id) in dict(await messages.list_countable_conversations(3))

    await messages.delete_for_user(sent["id"], user_id=3)

    assert int(conversation.id) not in dict(await messages.list_countable_conversations(3))
    assert int(conversation.id) in dict(await messages.list_countable_conversations(4))


async def test_disabled_not_countable(conversations, messages):
    """A disabled conversation leaves the count whatever its messages."""
    conversation = await conversations.create(int(ConversationType.GROUP), [3, 4], name="Course")

    assert int(conversation.id) in dict(await messages.list_countable_conversations(3))

    await conversations.update(int(conversation.id), enabled=0)

    assert await messages.list_countable_conversations(3) == []


async def test_self_conversation_countable(conversations, messages):
    """The just-created self conversation counts; the one emptied by deletions does not.
    """
    self_conv = await conversations.create(int(ConversationType.SELF), [3])

    assert int(self_conv.id) in dict(await messages.list_countable_conversations(3))

    sent = await messages.send(
        conversation_id=int(self_conv.id), user_id_from=3, text="Reminder", message_format=0
    )
    assert int(self_conv.id) in dict(await messages.list_countable_conversations(3))

    await messages.delete_for_user(sent["id"], user_id=3)

    assert int(self_conv.id) not in dict(await messages.list_countable_conversations(3))


async def test_count_unread_in_conversation(conversations, messages):
    """The per-conversation count excludes the reader's own messages and those they
    deleted.
    """
    first = await conversations.create(int(ConversationType.GROUP), [3, 4], name="Course")
    second = await conversations.create(int(ConversationType.GROUP), [3, 4], name="Other")

    await messages.send(
        conversation_id=int(first.id), user_id_from=4, text="one", message_format=0
    )
    deleted = await messages.send(
        conversation_id=int(first.id), user_id_from=4, text="two", message_format=0
    )
    await messages.send(
        conversation_id=int(first.id), user_id_from=3, text="mine", message_format=0
    )
    await messages.send(
        conversation_id=int(second.id), user_id_from=4, text="elsewhere", message_format=0
    )

    assert await messages.count_unread_in_conversation(3, int(first.id)) == 2

    await messages.delete_for_user(deleted["id"], user_id=3)

    assert await messages.count_unread_in_conversation(3, int(first.id)) == 1
    assert await messages.count_unread_in_conversation(3, int(second.id)) == 1
    assert await messages.count_unread_in_conversation(4, int(first.id)) == 1


async def test_search_sent_and_received(conversations, messages):
    """The search covers sent and received messages, and nothing else."""
    mine = await conversations.create(int(ConversationType.INDIVIDUAL), [3, 4])
    others = await conversations.create(int(ConversationType.INDIVIDUAL), [5, 6])

    await messages.send(
        conversation_id=int(mine.id), user_id_from=3, text="I wrote umbrella", message_format=0
    )
    await messages.send(
        conversation_id=int(mine.id), user_id_from=4, text="they wrote me umbrella", message_format=0
    )
    await messages.send(
        conversation_id=int(mine.id), user_id_from=4, text="without the word", message_format=0
    )
    await messages.send(
        conversation_id=int(others.id), user_id_from=5, text="umbrella among strangers", message_format=0
    )

    found = await messages.search(3, "umbrella")

    texts = sorted(row.small_message for row in found)
    assert texts == ["I wrote umbrella", "they wrote me umbrella"]


async def test_search_excludes_deleted(conversations, messages):
    """A deleted message is not found, and stays findable by the others."""
    conversation = await conversations.create(int(ConversationType.INDIVIDUAL), [3, 4])
    sent = await messages.send(
        conversation_id=int(conversation.id), user_id_from=4, text="searched word", message_format=0
    )

    await messages.delete_for_user(sent["id"], user_id=3)

    assert await messages.search(3, "word") == []
    assert len(await messages.search(4, "word")) == 1


async def test_search_group_rows(conversations, messages):
    """In a group the same message appears once per member, as in the source."""
    group = await conversations.create(int(ConversationType.GROUP), [3, 4, 5], name="Course")

    await messages.send(
        conversation_id=int(group.id), user_id_from=4, text="findme", message_format=0
    )

    found = await messages.search(3, "findme")

    # Three members, and the sender does not appear as recipient of themselves.
    assert sorted(row.user_id_to for row in found) == [3, 5]
