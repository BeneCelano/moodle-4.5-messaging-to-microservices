"""Use cases about messages."""

from __future__ import annotations

from collections.abc import Callable

from ..domain.constants import MessageAction
from ..domain.entities import Conversation, ConversationSummary, Message, MessageSearchResult
from ..domain.errors import (
    ConversationNotFound,
    MessageNotFound,
    NotAMember,
)
from ..domain.events import message_sent
from ..domain.ports import Clock, UnitOfWork

FORMAT_HTML = 1
"""Value Moodle uses for an HTML body, reproduced for contract compatibility."""


class MessageService:
    """Use cases for sending, reading and deleting messages."""

    def __init__(self, uow_factory: Callable[[], UnitOfWork], clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def send(
        self,
        conversation_id: int,
        user_id_from: int,
        text: str,
        message_format: int,
        correlation_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict:
        """Persist a message and record the event announcing it."""
        now = self._clock.now()

        async with self._uow_factory() as uow:
            if idempotency_key:
                already = await uow.commands.find_response(idempotency_key, "send_message")
                if already is not None:
                    return already

            conversation = await uow.conversations.get(conversation_id)
            if conversation is None:
                raise ConversationNotFound(conversation_id)

            member_ids = conversation.member_ids()
            if user_id_from not in member_ids:
                raise NotAMember(user_id_from, conversation_id)

            message = await uow.messages.add(
                Message.compose(
                    conversation_id=conversation_id,
                    user_id_from=user_id_from,
                    text=text,
                    message_format=message_format,
                    now=now,
                    html_format=FORMAT_HTML,
                )
            )

            uow.events.collect(
                message_sent(
                    message_id=int(message.id),
                    conversation_id=conversation_id,
                    conversation_type=int(conversation.type),
                    user_id_from=user_id_from,
                    recipient_ids=[uid for uid in member_ids if uid != user_id_from] or [user_id_from],
                    text=text,
                    message_format=message_format,
                    time_created=now,
                    correlation_id=correlation_id,
                )
            )

            # The full record is returned, not just the id.
            response = {
                "id": int(message.id),
                "conversation_id": conversation_id,
                "user_id_from": user_id_from,
                "text": message.full_message,
                "time_created": message.time_created,
                "full_message_trust": message.full_message_trust,
                "small_message": message.small_message,
                "full_message_format": message.full_message_format,
            }

            if idempotency_key:
                await uow.commands.record(idempotency_key, "send_message", response, now)

            await uow.commit()

        return response

    async def list_conversation_messages(
        self,
        user_id: int,
        conversation_id: int,
        time_deleted: int = 0,
        offset: int = 0,
        limit: int = 0,
        descending: bool = False,
        time_from: int = 0,
        time_to: int = 0,
    ) -> list[dict]:
        """Return the messages of the conversation visible to the user."""
        async with self._uow_factory() as uow:
            return await uow.messages.list_for_conversation(
                user_id, conversation_id, time_deleted, offset, limit, descending, time_from, time_to
            )

    async def get_message(self, message_id: int) -> Message:
        """Return a message."""
        async with self._uow_factory() as uow:
            message = await uow.messages.get(message_id)

        if message is None:
            raise MessageNotFound(message_id)
        return message

    async def mark_as_read(self, message_id: int, user_id: int, time_read: int | None = None) -> int:
        """Record that a user read a message."""
        async with self._uow_factory() as uow:
            message = await uow.messages.get(message_id)
            if message is None:
                raise MessageNotFound(message_id)

            action_id = await uow.messages.record_action(
                user_id, message_id, int(MessageAction.READ), time_read or self._clock.now()
            )
            await uow.commit()

        return action_id

    async def mark_all_as_read(self, user_id: int, conversation_id: int | None = None) -> list[dict]:
        """Record the reading of every received and still unread message."""
        now = self._clock.now()
        marked: list[dict] = []

        async with self._uow_factory() as uow:
            for message in await uow.messages.list_unread(user_id, conversation_id):
                action_id = await uow.messages.record_action(
                    user_id, int(message.id), int(MessageAction.READ), now
                )
                marked.append(
                    {
                        "message_id": int(message.id),
                        "user_id_from": message.user_id_from,
                        "action_id": action_id,
                    }
                )
            await uow.commit()

        return marked

    async def delete_for_user(self, message_id: int, user_id: int) -> int | None:
        """Record the deletion of a message for one user."""
        async with self._uow_factory() as uow:
            message = await uow.messages.get(message_id)
            if message is None:
                return None

            if await uow.messages.has_action(user_id, message_id, int(MessageAction.DELETED)):
                return None

            action_id = await uow.messages.record_action(
                user_id, message_id, int(MessageAction.DELETED), self._clock.now()
            )
            await uow.commit()

        return action_id

    async def delete_for_all_users(self, message_id: int) -> list[dict]:
        """Record the deletion of a message for every member of the conversation."""
        now = self._clock.now()
        deleted: list[dict] = []

        async with self._uow_factory() as uow:
            message = await uow.messages.get(message_id)
            if message is None:
                return []

            for member_id in await uow.conversations.member_ids(message.conversation_id):
                if await uow.messages.has_action(member_id, message_id, int(MessageAction.DELETED)):
                    continue
                action_id = await uow.messages.record_action(
                    member_id, message_id, int(MessageAction.DELETED), now
                )
                deleted.append(
                    {"user_id": member_id, "message_id": message_id, "action_id": action_id}
                )
            await uow.commit()

        return deleted

    async def delete_conversation_for_user(self, conversation_id: int, user_id: int) -> list[dict]:
        """Record the deletion, for one user, of every message of a conversation."""
        now = self._clock.now()
        deleted: list[dict] = []

        async with self._uow_factory() as uow:
            messages = await uow.messages.list_undeleted_in_conversation(user_id, conversation_id)
            for message in messages:
                action_id = await uow.messages.record_action(
                    user_id, int(message.id), int(MessageAction.DELETED), now
                )
                deleted.append(
                    {"user_id": user_id, "message_id": int(message.id), "action_id": action_id}
                )
            await uow.commit()

        return deleted

    async def count_unread_conversations(self, user_id: int) -> int:
        async with self._uow_factory() as uow:
            return await uow.messages.count_unread_conversations(user_id)

    async def search(
        self, user_id: int, text: str, offset: int = 0, limit: int = 0
    ) -> list[MessageSearchResult]:
        """Search the messages the user sent or received, most recent first."""
        async with self._uow_factory() as uow:
            return await uow.messages.search(user_id, text, offset, limit)

    async def count_unread_in_conversation(self, user_id: int, conversation_id: int) -> int:
        """Count the unread messages of a conversation, excluding those sent by the
        user.
        """
        async with self._uow_factory() as uow:
            return await uow.messages.count_unread_in_conversation(user_id, conversation_id)

    async def conversation_of(self, message_id: int) -> Conversation | None:
        """Return the conversation the message belongs to, if the message exists."""
        async with self._uow_factory() as uow:
            message = await uow.messages.get(message_id)
            if message is None:
                return None

            return await uow.conversations.get(message.conversation_id)

    async def list_conversations(
        self,
        user_id: int,
        offset: int = 0,
        limit: int = 0,
        conversation_type: int | None = None,
        merge_self: bool = False,
        favourite_ids: list[int] | None = None,
        only_favourites: bool | None = None,
    ) -> list[ConversationSummary]:
        """Compose the list of a user's conversations, most recent first."""
        async with self._uow_factory() as uow:
            return await uow.messages.list_conversations(
                user_id,
                offset=offset,
                limit=limit,
                conversation_type=conversation_type,
                merge_self=merge_self,
                favourite_ids=favourite_ids,
                only_favourites=only_favourites,
            )

    async def list_countable_conversations(self, user_id: int) -> list[tuple[int, int]]:
        async with self._uow_factory() as uow:
            return await uow.messages.list_countable_conversations(user_id)

    async def list_unread_conversations(self, user_id: int) -> list[tuple[int, int]]:
        """List the conversations with at least one unread message, each with its type.
        """
        async with self._uow_factory() as uow:
            return await uow.messages.list_unread_conversations(user_id)
