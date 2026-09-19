"""Use cases of the conversation aggregate."""

from __future__ import annotations

from collections.abc import Callable

from ..domain.constants import ConversationAction, ConversationState, ConversationType
from ..domain.entities import Conversation, conversation_hash
from ..domain.errors import ConversationNotFound, InvalidConversation
from ..domain.events import conversation_created
from ..domain.ports import Clock, UnitOfWork


class ConversationService:
    """Use cases about conversations and their membership."""

    def __init__(self, uow_factory: Callable[[], UnitOfWork], clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def create(
        self,
        conversation_type: int,
        user_ids: list[int],
        name: str | None = None,
        enabled: int = int(ConversationState.ENABLED),
        component: str | None = None,
        item_type: str | None = None,
        item_id: int | None = None,
        context_id: int | None = None,
    ) -> Conversation:
        """Create a conversation, returning the existing one for individual and self
        types.
        """
        try:
            requested_type = ConversationType(conversation_type)
        except ValueError as error:
            raise InvalidConversation("An invalid conversation type was specified.") from error

        now = self._clock.now()

        async with self._uow_factory() as uow:
            if requested_type in (ConversationType.INDIVIDUAL, ConversationType.SELF):
                existing = await uow.conversations.get_by_hash(conversation_hash(user_ids))
                if existing is not None:
                    return existing

            conversation = Conversation.create(
                requested_type,
                user_ids,
                now,
                name=name,
                enabled=enabled,
                component=component,
                item_type=item_type,
                item_id=item_id,
                context_id=context_id,
            )
            conversation = await uow.conversations.add(conversation)

            uow.events.collect(
                conversation_created(
                    conversation_id=int(conversation.id),
                    conversation_type=int(conversation.type),
                    member_ids=conversation.member_ids(),
                    time_created=conversation.time_created,
                )
            )
            await uow.commit()

        return conversation

    async def get(self, conversation_id: int) -> Conversation:
        """Return a conversation with its members."""
        async with self._uow_factory() as uow:
            conversation = await uow.conversations.get(conversation_id)

        if conversation is None:
            raise ConversationNotFound(conversation_id)
        return conversation

    async def get_individual(self, user_ids: list[int]) -> Conversation:
        return await self._get_by_hash(user_ids)

    async def get_self(self, user_id: int) -> Conversation:
        return await self._get_by_hash([user_id])

    async def _get_by_hash(self, user_ids: list[int]) -> Conversation:
        """Return the conversation identified by the hash of its members."""
        async with self._uow_factory() as uow:
            conversation = await uow.conversations.get_by_hash(conversation_hash(user_ids))

        if conversation is None:
            raise ConversationNotFound(0)
        return conversation

    async def get_by_area(
        self, component: str, item_type: str, item_id: int, context_id: int
    ) -> Conversation:
        """Return the conversation linked to an item of another component."""
        async with self._uow_factory() as uow:
            conversation = await uow.conversations.get_by_area(component, item_type, item_id, context_id)

        if conversation is None:
            raise ConversationNotFound(item_id)
        return conversation

    async def find_shared(
        self, user_id: int, other_user_id: int, offset: int = 0, limit: int = 20
    ) -> list[Conversation]:
        """Return the enabled conversations shared by two users."""
        if user_id == other_user_id:
            return []

        async with self._uow_factory() as uow:
            return await uow.conversations.find_shared(user_id, other_user_id, offset, limit)

    async def update(
        self, conversation_id: int, enabled: int | None = None, name: str | None = None
    ) -> Conversation:
        """Update the enabled state and the name of a conversation."""
        async with self._uow_factory() as uow:
            conversation = await uow.conversations.get(conversation_id)
            if conversation is None:
                raise ConversationNotFound(conversation_id)

            await uow.conversations.update_state(conversation_id, enabled, name, self._clock.now())
            await uow.commit()

            updated = await uow.conversations.get(conversation_id)

        if updated is None:
            raise ConversationNotFound(conversation_id)
        return updated

    async def delete_all_data(self, conversation_id: int) -> None:
        """Remove every datum owned by this context for the given conversation."""
        async with self._uow_factory() as uow:
            await uow.messages.delete_by_conversation(conversation_id)
            await uow.conversations.delete(conversation_id)
            await uow.commit()

    async def member_ids(self, conversation_id: int, offset: int = 0, limit: int = 0) -> list[int]:
        """Identifiers of the members."""
        async with self._uow_factory() as uow:
            return await uow.conversations.member_ids(conversation_id, offset, limit)

    async def is_member(self, conversation_id: int, user_id: int) -> bool:
        """Whether the user is a member of the conversation."""
        async with self._uow_factory() as uow:
            return await uow.conversations.is_member(conversation_id, user_id)

    async def add_members(self, conversation_id: int, user_ids: list[int]) -> int:
        """Add members to a group conversation."""
        async with self._uow_factory() as uow:
            conversation = await uow.conversations.get(conversation_id)
            if conversation is None:
                raise ConversationNotFound(conversation_id)
            if conversation.type is not ConversationType.GROUP:
                raise InvalidConversation("You can not add members to a non-group conversation.")

            added = await uow.conversations.add_members(conversation_id, user_ids, self._clock.now())
            await uow.commit()

        return added

    async def remove_members(self, conversation_id: int, user_ids: list[int]) -> int:
        """Remove members from a group conversation."""
        async with self._uow_factory() as uow:
            conversation = await uow.conversations.get(conversation_id)
            if conversation is None:
                raise ConversationNotFound(conversation_id)
            if conversation.type is not ConversationType.GROUP:
                raise InvalidConversation("You can not remove members from a non-group conversation.")

            removed = await uow.conversations.remove_members(conversation_id, user_ids)
            await uow.commit()

        return removed

    async def count_members(self, conversation_id: int) -> int:
        async with self._uow_factory() as uow:
            return await uow.conversations.count_members(conversation_id)

    async def mute(self, conversation_id: int, user_id: int) -> None:
        """Mute a conversation for a user."""
        async with self._uow_factory() as uow:
            await uow.conversations.set_action(
                conversation_id, user_id, int(ConversationAction.MUTED), self._clock.now()
            )
            await uow.commit()

    async def unmute(self, conversation_id: int, user_id: int) -> None:
        """Unmute a conversation for a user."""
        async with self._uow_factory() as uow:
            await uow.conversations.clear_action(conversation_id, user_id, int(ConversationAction.MUTED))
            await uow.commit()

    async def is_muted(self, conversation_id: int, user_id: int) -> bool:
        """Whether a conversation is muted for a user."""
        async with self._uow_factory() as uow:
            return await uow.conversations.has_action(
                conversation_id, user_id, int(ConversationAction.MUTED)
            )
