"""Repository implementations over async SQLAlchemy."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import case, delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..domain.constants import (
    ConversationAction,
    ConversationState,
    ConversationType,
    MessageAction,
)
from ..domain.entities import (
    Contact,
    ContactRequest,
    Conversation,
    ConversationSummary,
    Member,
    Message,
    MessageSearchResult,
    UserBlock,
    conversation_hash,
)
from .models import (
    ContactRequestRow,
    ContactRow,
    ConversationActionRow,
    ConversationMemberRow,
    ConversationRow,
    MessageRow,
    MessageUserActionRow,
    ProcessedCommandRow,
    UserBlockRow,
    UserViewRow,
)

logger = logging.getLogger(__name__)
"""Reports the anomalies the repository tolerates instead of failing the request."""


def _to_conversation(row: ConversationRow, members: list[int] | None = None) -> Conversation:
    return Conversation(
        id=row.id,
        type=ConversationType(row.type),
        name=row.name,
        conversation_hash=row.conversation_hash,
        component=row.component,
        item_type=row.item_type,
        item_id=row.item_id,
        context_id=row.context_id,
        enabled=row.enabled,
        time_created=row.time_created,
        time_modified=row.time_modified,
        members=[Member(user_id=uid, conversation_id=row.id) for uid in (members or [])],
    )


def _to_message(row: MessageRow) -> Message:
    return Message(
        id=row.id,
        conversation_id=row.conversation_id,
        user_id_from=row.user_id_from,
        text=row.full_message or "",
        subject=row.subject,
        full_message=row.full_message,
        full_message_format=row.full_message_format,
        full_message_html=row.full_message_html,
        small_message=row.small_message,
        full_message_trust=row.full_message_trust,
        custom_data=row.custom_data,
        time_created=row.time_created,
    )


class SqlConversationRepository:
    """Repository of the conversation aggregate."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, conversation: Conversation) -> Conversation:
        """Persist a conversation and its initial members."""
        row = ConversationRow(
            type=int(conversation.type),
            name=conversation.name,
            conversation_hash=conversation.conversation_hash,
            component=conversation.component,
            item_type=conversation.item_type,
            item_id=conversation.item_id,
            context_id=conversation.context_id,
            enabled=conversation.enabled,
            time_created=conversation.time_created,
            time_modified=conversation.time_modified,
        )
        self._session.add(row)
        await self._session.flush()

        for member in conversation.members:
            self._session.add(
                ConversationMemberRow(
                    conversation_id=row.id,
                    user_id=member.user_id,
                    time_created=member.time_created or conversation.time_created,
                )
            )
        await self._session.flush()

        conversation.id = row.id
        for member in conversation.members:
            member.conversation_id = row.id
        return conversation

    async def get(self, conversation_id: int) -> Conversation | None:
        """Return a conversation with its members."""
        row = await self._session.get(ConversationRow, conversation_id)
        if row is None:
            return None
        return _to_conversation(row, await self.member_ids(conversation_id))

    async def get_by_hash(self, conversation_hash: str) -> Conversation | None:
        """Return the conversation identified by the hash of its members."""
        result = await self._session.execute(
            select(ConversationRow)
            .where(ConversationRow.conversation_hash == conversation_hash)
            .order_by(ConversationRow.id)
            .limit(2)
        )
        rows = result.scalars().all()
        if not rows:
            return None

        if len(rows) > 1:
            logger.warning(
                "Duplicate hash: several conversations share %s, the oldest is used (%s)",
                conversation_hash,
                rows[0].id,
            )

        return _to_conversation(rows[0], await self.member_ids(rows[0].id))

    async def get_by_area(
        self, component: str, item_type: str, item_id: int, context_id: int
    ) -> Conversation | None:
        """Return the conversation linked to an item of another component."""
        # This index is not unique either: the oldest is returned, as in get_by_hash.
        result = await self._session.execute(
            select(ConversationRow)
            .where(
                ConversationRow.component == component,
                ConversationRow.item_type == item_type,
                ConversationRow.item_id == item_id,
                ConversationRow.context_id == context_id,
            )
            .order_by(ConversationRow.id)
            .limit(2)
        )
        rows = result.scalars().all()
        if not rows:
            return None

        if len(rows) > 1:
            logger.warning(
                "Duplicate area: several conversations linked to %s/%s/%s, the oldest is used (%s)",
                component,
                item_type,
                item_id,
                rows[0].id,
            )

        return _to_conversation(rows[0])

    async def find_shared(
        self, user_id: int, other_user_id: int, offset: int, limit: int
    ) -> list[Conversation]:
        """Return the enabled conversations of which both users are members."""
        first = ConversationMemberRow.__table__.alias("m1")
        second = ConversationMemberRow.__table__.alias("m2")

        statement = (
            select(ConversationRow)
            .join(first, first.c.conversation_id == ConversationRow.id)
            .join(second, second.c.conversation_id == ConversationRow.id)
            .where(
                first.c.user_id == user_id,
                second.c.user_id == other_user_id,
                ConversationRow.enabled != int(ConversationState.DISABLED),
            )
            .order_by(ConversationRow.time_created.desc())
        )
        if limit:
            statement = statement.limit(limit).offset(offset)

        result = await self._session.execute(statement)
        return [_to_conversation(row) for row in result.scalars().all()]

    async def update_state(
        self, conversation_id: int, enabled: int | None, name: str | None, now: int
    ) -> None:
        """Update the enabled state and the name, leaving untouched what was not given.
        """
        values: dict[str, Any] = {"time_modified": now}
        if enabled is not None:
            values["enabled"] = enabled
        if name is not None:
            values["name"] = name

        await self._session.execute(
            update(ConversationRow).where(ConversationRow.id == conversation_id).values(**values)
        )

    async def delete(self, conversation_id: int) -> None:
        """Remove the conversation, its members and the actions about it."""
        await self._session.execute(
            delete(ConversationMemberRow).where(ConversationMemberRow.conversation_id == conversation_id)
        )
        await self._session.execute(
            delete(ConversationActionRow).where(ConversationActionRow.conversation_id == conversation_id)
        )
        await self._session.execute(delete(ConversationRow).where(ConversationRow.id == conversation_id))

    async def member_ids(self, conversation_id: int, offset: int = 0, limit: int = 0) -> list[int]:
        """Identifiers of the members, oldest first."""
        statement = (
            select(ConversationMemberRow.user_id)
            .where(ConversationMemberRow.conversation_id == conversation_id)
            .order_by(ConversationMemberRow.time_created.asc(), ConversationMemberRow.id.asc())
        )
        if limit:
            statement = statement.limit(limit).offset(offset)

        result = await self._session.execute(statement)
        return [int(uid) for uid in result.scalars().all()]

    async def is_member(self, conversation_id: int, user_id: int) -> bool:
        """Whether the user is a member of the conversation."""
        result = await self._session.execute(
            select(ConversationMemberRow.id).where(
                ConversationMemberRow.conversation_id == conversation_id,
                ConversationMemberRow.user_id == user_id,
            )
        )
        return result.first() is not None

    async def add_members(self, conversation_id: int, user_ids: list[int], now: int) -> int:
        """Add members, ignoring those already present."""
        existing = set(await self.member_ids(conversation_id))
        new_ids = [uid for uid in dict.fromkeys(user_ids) if uid not in existing]

        for user_id in new_ids:
            self._session.add(
                ConversationMemberRow(
                    conversation_id=conversation_id, user_id=user_id, time_created=now
                )
            )
        if new_ids:
            await self._session.flush()

        return len(new_ids)

    async def remove_members(self, conversation_id: int, user_ids: list[int]) -> int:
        """Remove the given members."""
        if not user_ids:
            return 0

        result = await self._session.execute(
            delete(ConversationMemberRow).where(
                ConversationMemberRow.conversation_id == conversation_id,
                ConversationMemberRow.user_id.in_(user_ids),
            )
        )
        return int(result.rowcount or 0)

    async def count_members(self, conversation_id: int) -> int:
        result = await self._session.execute(
            select(func.count(ConversationMemberRow.id)).where(
                ConversationMemberRow.conversation_id == conversation_id
            )
        )
        return int(result.scalar_one())

    async def set_action(self, conversation_id: int, user_id: int, action: int, now: int) -> None:
        """Record an action of the user on the conversation, without duplicating it."""
        if await self.has_action(conversation_id, user_id, action):
            return

        self._session.add(
            ConversationActionRow(
                conversation_id=conversation_id, user_id=user_id, action=action, time_created=now
            )
        )
        await self._session.flush()

    async def clear_action(self, conversation_id: int, user_id: int, action: int) -> None:
        """Remove an action of the user on the conversation."""
        await self._session.execute(
            delete(ConversationActionRow).where(
                ConversationActionRow.conversation_id == conversation_id,
                ConversationActionRow.user_id == user_id,
                ConversationActionRow.action == action,
            )
        )

    async def has_action(self, conversation_id: int, user_id: int, action: int) -> bool:
        """Whether the user has recorded that action on the conversation."""
        result = await self._session.execute(
            select(ConversationActionRow.id).where(
                ConversationActionRow.conversation_id == conversation_id,
                ConversationActionRow.user_id == user_id,
                ConversationActionRow.action == action,
            )
        )
        return result.first() is not None


class SqlMessageRepository:
    """Repository of messages and of the per-user state."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, message: Message) -> Message:
        """Persist a message."""
        row = MessageRow(
            conversation_id=message.conversation_id,
            user_id_from=message.user_id_from,
            subject=message.subject,
            full_message=message.full_message,
            full_message_format=message.full_message_format,
            full_message_html=message.full_message_html,
            small_message=message.small_message,
            full_message_trust=message.full_message_trust,
            custom_data=message.custom_data,
            time_created=message.time_created,
        )
        self._session.add(row)
        await self._session.flush()

        message.id = row.id
        return message

    async def get(self, message_id: int) -> Message | None:
        """Return a message."""
        result = await self._session.execute(select(MessageRow).where(MessageRow.id == message_id))
        row = result.scalars().first()
        return _to_message(row) if row is not None else None

    async def list_for_conversation(
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
        """Return the messages visible to the user, with the instant they read them."""
        read_action = MessageUserActionRow.__table__.alias("read_action")
        delete_action = MessageUserActionRow.__table__.alias("delete_action")

        statement = (
            select(
                MessageRow,
                read_action.c.time_created.label("time_read"),
                ConversationRow.context_id.label("context_id"),
            )
            .join(ConversationRow, ConversationRow.id == MessageRow.conversation_id)
            .join(
                read_action,
                (read_action.c.message_id == MessageRow.id)
                & (read_action.c.user_id == user_id)
                & (read_action.c.action == int(MessageAction.READ)),
                isouter=True,
            )
            .where(MessageRow.conversation_id == conversation_id)
        )

        if time_deleted:
            statement = statement.join(
                delete_action,
                (delete_action.c.message_id == MessageRow.id)
                & (delete_action.c.user_id == user_id)
                & (delete_action.c.action == int(MessageAction.DELETED))
                & (delete_action.c.time_created == time_deleted),
            )
        else:
            statement = statement.join(
                delete_action,
                (delete_action.c.message_id == MessageRow.id)
                & (delete_action.c.user_id == user_id)
                & (delete_action.c.action == int(MessageAction.DELETED)),
                isouter=True,
            ).where(delete_action.c.id.is_(None))

        if time_from:
            statement = statement.where(MessageRow.time_created >= time_from)
        if time_to:
            statement = statement.where(MessageRow.time_created <= time_to)

        order = MessageRow.time_created.desc() if descending else MessageRow.time_created.asc()
        statement = statement.order_by(order, MessageRow.id.asc())
        if limit:
            statement = statement.limit(limit).offset(offset)

        result = await self._session.execute(statement)

        messages = []
        for row, time_read, context_id in result.all():
            messages.append(
                {
                    "id": row.id,
                    "user_id_from": row.user_id_from,
                    "subject": row.subject,
                    "full_message": row.full_message,
                    "full_message_html": row.full_message_html,
                    "full_message_format": row.full_message_format,
                    "full_message_trust": row.full_message_trust,
                    "small_message": row.small_message,
                    "time_created": row.time_created,
                    "context_id": context_id,
                    "time_read": time_read,
                }
            )
        return messages

    async def record_action(self, user_id: int, message_id: int, action: int, now: int) -> int:
        """Record an action of the user on a message and return its id."""
        already_recorded = await self._session.execute(
            select(MessageUserActionRow.id).where(
                MessageUserActionRow.user_id == user_id,
                MessageUserActionRow.message_id == message_id,
                MessageUserActionRow.action == action,
            )
        )
        found = already_recorded.scalar_one_or_none()
        if found is not None:
            return int(found)

        row = MessageUserActionRow(
            user_id=user_id, message_id=message_id, action=action, time_created=now
        )
        try:
            # The savepoint isolates the conflict of two concurrent requests recording
            # the same action.
            async with self._session.begin_nested():
                self._session.add(row)
                await self._session.flush()
        except IntegrityError:
            existing = await self._session.execute(
                select(MessageUserActionRow.id).where(
                    MessageUserActionRow.user_id == user_id,
                    MessageUserActionRow.message_id == message_id,
                    MessageUserActionRow.action == action,
                )
            )
            return int(existing.scalar_one())

        return int(row.id)

    async def has_action(self, user_id: int, message_id: int, action: int) -> bool:
        """Whether the user has already recorded that action on the message."""
        result = await self._session.execute(
            select(MessageUserActionRow.id).where(
                MessageUserActionRow.user_id == user_id,
                MessageUserActionRow.message_id == message_id,
                MessageUserActionRow.action == action,
            )
        )
        return result.first() is not None

    async def list_unread(self, user_id: int, conversation_id: int | None) -> list[Message]:
        """Return the messages received by the user and not yet read."""
        read_action = MessageUserActionRow.__table__.alias("read_action")

        statement = (
            select(MessageRow)
            .join(
                ConversationMemberRow,
                (ConversationMemberRow.conversation_id == MessageRow.conversation_id)
                & (ConversationMemberRow.user_id == user_id),
            )
            .join(
                read_action,
                (read_action.c.message_id == MessageRow.id)
                & (read_action.c.user_id == user_id)
                & (read_action.c.action == int(MessageAction.READ)),
                isouter=True,
            )
            .where(read_action.c.id.is_(None), MessageRow.user_id_from != user_id)
        )
        if conversation_id is not None:
            statement = statement.where(MessageRow.conversation_id == conversation_id)

        result = await self._session.execute(statement)
        return [_to_message(row) for row in result.scalars().all()]

    async def list_undeleted_in_conversation(self, user_id: int, conversation_id: int) -> list[Message]:
        """Return the messages of the conversation the user has not deleted yet."""
        delete_action = MessageUserActionRow.__table__.alias("delete_action")

        statement = (
            select(MessageRow)
            .join(
                delete_action,
                (delete_action.c.message_id == MessageRow.id)
                & (delete_action.c.user_id == user_id)
                & (delete_action.c.action == int(MessageAction.DELETED)),
                isouter=True,
            )
            .where(delete_action.c.id.is_(None), MessageRow.conversation_id == conversation_id)
            .order_by(MessageRow.time_created.asc())
        )

        result = await self._session.execute(statement)
        return [_to_message(row) for row in result.scalars().all()]

    async def count_unread_conversations(self, user_id: int) -> int:
        """Count the enabled conversations with at least one unread message."""
        read_action = MessageUserActionRow.__table__.alias("read_action")

        statement = (
            select(func.count(func.distinct(MessageRow.conversation_id)))
            .select_from(MessageRow)
            .join(ConversationRow, ConversationRow.id == MessageRow.conversation_id)
            .join(
                ConversationMemberRow,
                (ConversationMemberRow.conversation_id == MessageRow.conversation_id)
                & (ConversationMemberRow.user_id == user_id),
            )
            .join(
                read_action,
                (read_action.c.message_id == MessageRow.id)
                & (read_action.c.user_id == user_id)
                & (read_action.c.action == int(MessageAction.READ)),
                isouter=True,
            )
            .where(
                read_action.c.id.is_(None),
                ConversationRow.enabled == int(ConversationState.ENABLED),
                MessageRow.user_id_from != user_id,
            )
        )

        result = await self._session.execute(statement)
        return int(result.scalar_one())

    async def list_unread_conversations(self, user_id: int) -> list[tuple[int, int]]:
        """List the enabled conversations with at least one unread message, with their
        type.
        """
        action = MessageUserActionRow.__table__.alias("user_action")

        statement = (
            select(MessageRow.conversation_id, ConversationRow.type)
            .select_from(MessageRow)
            .join(ConversationRow, ConversationRow.id == MessageRow.conversation_id)
            .join(
                ConversationMemberRow,
                (ConversationMemberRow.conversation_id == MessageRow.conversation_id)
                & (ConversationMemberRow.user_id == user_id),
            )
            .join(
                action,
                (action.c.message_id == MessageRow.id)
                & (action.c.user_id == user_id)
                & (
                    (action.c.action == int(MessageAction.READ))
                    | (action.c.action == int(MessageAction.DELETED))
                ),
                isouter=True,
            )
            .where(
                action.c.id.is_(None),
                ConversationRow.enabled == int(ConversationState.ENABLED),
                MessageRow.user_id_from != user_id,
            )
            .group_by(MessageRow.conversation_id, ConversationRow.type)
        )

        result = await self._session.execute(statement)
        return [(int(cid), int(ctype)) for cid, ctype in result.all()]

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
        message = MessageRow.__table__.alias("last_visible")
        mute = ConversationActionRow.__table__.alias("mute")
        last_message = self._last_visible_message_subquery(user_id)

        statement = (
            select(
                ConversationRow.id.label("conv_id"),
                ConversationRow.type.label("conv_type"),
                ConversationRow.name.label("conv_name"),
                ConversationRow.component.label("conv_component"),
                ConversationRow.item_type.label("conv_item_type"),
                ConversationRow.item_id.label("conv_item_id"),
                ConversationRow.context_id.label("conv_context_id"),
                mute.c.action.label("muted"),
                message.c.id.label("msg_id"),
                message.c.user_id_from.label("msg_user_id_from"),
                message.c.small_message.label("msg_small"),
                message.c.full_message.label("msg_full"),
                message.c.full_message_format.label("msg_format"),
                message.c.full_message_html.label("msg_html"),
                message.c.full_message_trust.label("msg_trust"),
                message.c.time_created.label("msg_time"),
            )
            .join(
                ConversationMemberRow,
                (ConversationMemberRow.conversation_id == ConversationRow.id)
                & (ConversationMemberRow.user_id == user_id),
            )
            .join(
                last_message,
                last_message.c.conversation_id == ConversationRow.id,
                isouter=True,
            )
            .join(message, message.c.id == last_message.c.message_id, isouter=True)
            .join(
                mute,
                (mute.c.conversation_id == ConversationRow.id)
                & (mute.c.user_id == user_id)
                & (mute.c.action == int(ConversationAction.MUTED)),
                isouter=True,
            )
            .where(ConversationRow.enabled == int(ConversationState.ENABLED))
        )

        if conversation_type is not None:
            if merge_self and conversation_type == int(ConversationType.INDIVIDUAL):
                # Self conversations are listed with the individual ones when asked.
                statement = statement.where(
                    ConversationRow.type.in_(
                        [int(ConversationType.INDIVIDUAL), int(ConversationType.SELF)]
                    )
                )
            else:
                statement = statement.where(ConversationRow.type == conversation_type)

        if only_favourites is not None and favourite_ids:
            if only_favourites:
                statement = statement.where(ConversationRow.id.in_(favourite_ids))
            else:
                statement = statement.where(ConversationRow.id.not_in(favourite_ids))

        # Conversations without messages go last (NULL ordering differs between
        # engines).
        statement = statement.order_by(
            case((message.c.time_created.is_(None), 0), else_=1).desc(),
            message.c.time_created.desc(),
            ConversationRow.id.desc(),
        )

        if limit:
            statement = statement.limit(limit).offset(offset)

        rows = (await self._session.execute(statement)).all()
        if not rows:
            return []

        ids = [int(row.conv_id) for row in rows]
        member_counts = await self._member_counts(ids)
        unread = await self._unread_counts(user_id, ids)
        representatives = await self._representative_members(user_id, rows)
        self_totals = await self._self_conversation_message_total(user_id)

        entries: list[ConversationSummary] = []
        for row in rows:
            ctype = int(row.conv_type)
            no_visible_message = row.msg_id is None

            # An emptied individual or self conversation leaves the list; a never used
            # self conversation and a group stay.
            if ctype == int(ConversationType.INDIVIDUAL) and no_visible_message:
                continue
            if (
                ctype == int(ConversationType.SELF)
                and no_visible_message
                and self_totals > 0
            ):
                continue

            conversation_id = int(row.conv_id)
            last = None
            if not no_visible_message:
                last = Message(
                    id=int(row.msg_id),
                    conversation_id=conversation_id,
                    user_id_from=int(row.msg_user_id_from),
                    text=row.msg_small or "",
                    time_created=int(row.msg_time),
                    full_message=row.msg_full,
                    full_message_format=int(row.msg_format or 0),
                    full_message_html=row.msg_html,
                    small_message=row.msg_small,
                    full_message_trust=int(row.msg_trust or 0),
                )

            entries.append(
                ConversationSummary(
                    id=conversation_id,
                    type=ctype,
                    name=row.conv_name,
                    component=row.conv_component,
                    item_type=row.conv_item_type,
                    item_id=row.conv_item_id,
                    context_id=row.conv_context_id,
                    is_muted=row.muted is not None,
                    member_count=member_counts.get(conversation_id, 0),
                    unread_count=unread.get(conversation_id, 0),
                    member_ids=representatives.get(conversation_id, []),
                    last_message=last,
                )
            )

        return entries

    def _last_visible_message_subquery(self, user_id: int):
        """For each conversation, the last message the user has not deleted."""
        deleted = (
            select(MessageUserActionRow.id)
            .where(
                MessageUserActionRow.message_id == MessageRow.id,
                MessageUserActionRow.user_id == user_id,
                MessageUserActionRow.action == int(MessageAction.DELETED),
            )
            .exists()
        )

        last_instant = (
            select(
                MessageRow.conversation_id.label("conversation_id"),
                func.max(MessageRow.time_created).label("max_time"),
            )
            .where(~deleted)
            .group_by(MessageRow.conversation_id)
            .subquery()
        )

        return (
            select(
                MessageRow.conversation_id.label("conversation_id"),
                func.max(MessageRow.id).label("message_id"),
            )
            .join(
                last_instant,
                (last_instant.c.conversation_id == MessageRow.conversation_id)
                & (last_instant.c.max_time == MessageRow.time_created),
            )
            .group_by(MessageRow.conversation_id)
            .subquery()
        )

    async def _member_counts(self, conversation_ids: list[int]) -> dict[int, int]:
        """Count the members of each conversation."""
        statement = (
            select(
                ConversationMemberRow.conversation_id,
                func.count(func.distinct(ConversationMemberRow.user_id)),
            )
            .where(ConversationMemberRow.conversation_id.in_(conversation_ids))
            .group_by(ConversationMemberRow.conversation_id)
        )

        result = await self._session.execute(statement)
        return {int(cid): int(n) for cid, n in result.all()}

    async def _unread_counts(self, user_id: int, conversation_ids: list[int]) -> dict[int, int]:
        """Count the unread messages of each conversation, excluding those sent by the
        user.
        """
        action = MessageUserActionRow.__table__.alias("reading_action")

        statement = (
            select(MessageRow.conversation_id, func.count(MessageRow.id))
            .join(
                action,
                (action.c.message_id == MessageRow.id)
                & (action.c.user_id == user_id)
                & (
                    (action.c.action == int(MessageAction.READ))
                    | (action.c.action == int(MessageAction.DELETED))
                ),
                isouter=True,
            )
            .where(
                action.c.id.is_(None),
                MessageRow.conversation_id.in_(conversation_ids),
                MessageRow.user_id_from != user_id,
            )
            .group_by(MessageRow.conversation_id)
        )

        result = await self._session.execute(statement)
        return {int(cid): int(n) for cid, n in result.all()}

    async def _representative_members(self, user_id: int, rows) -> dict[int, list[int]]:
        """For each conversation, the members the list must show: the other end for an individual one,
        the last writer for a group (nobody if it has no messages), the user for a self one.
        """
        representatives: dict[int, list[int]] = {}
        to_resolve: list[int] = []

        for row in rows:
            conversation_id = int(row.conv_id)
            ctype = int(row.conv_type)
            author = None if row.msg_user_id_from is None else int(row.msg_user_id_from)

            if ctype == int(ConversationType.SELF):
                representatives[conversation_id] = [user_id]
            elif ctype == int(ConversationType.GROUP):
                representatives[conversation_id] = [] if author is None else [author]
            elif author is not None and author != user_id:
                representatives[conversation_id] = [author]
            else:
                # Individual conversation whose last message is the user's own, or with
                # no message: the other end is looked up among the members.
                representatives[conversation_id] = []
                to_resolve.append(conversation_id)

        if to_resolve:
            statement = (
                select(ConversationMemberRow.conversation_id, ConversationMemberRow.user_id)
                .where(
                    ConversationMemberRow.conversation_id.in_(to_resolve),
                    ConversationMemberRow.user_id != user_id,
                )
                .order_by(ConversationMemberRow.id.asc())
            )
            for conversation_id, member in (await self._session.execute(statement)).all():
                representatives[int(conversation_id)].append(int(member))

        return representatives

    async def _self_conversation_message_total(self, user_id: int) -> int:
        """Count the messages ever written in the user's self conversation, to tell one emptied by
        deletions (hidden) from one just created (shown).
        """
        statement = (
            select(func.count(MessageRow.id))
            .join(ConversationRow, ConversationRow.id == MessageRow.conversation_id)
            .where(
                ConversationRow.type == int(ConversationType.SELF),
                ConversationRow.conversation_hash == conversation_hash([user_id]),
            )
        )

        result = await self._session.execute(statement)
        return int(result.scalar_one())

    async def search(
        self, user_id: int, text: str, offset: int = 0, limit: int = 0
    ) -> list[MessageSearchResult]:
        """Search the messages the user sent or received, most recent first."""
        sender = UserViewRow.__table__.alias("sender")
        recipient = UserViewRow.__table__.alias("recipient")
        sender_block = UserBlockRow.__table__.alias("sender_block")
        recipient_block = UserBlockRow.__table__.alias("recipient_block")
        deletion = MessageUserActionRow.__table__.alias("deletion")

        # Messages sent by the user plus those of their conversations (UNION); the
        # members table is aliased to avoid the automatic correlation.
        membership = ConversationMemberRow.__table__.alias("membership")
        is_member_of = (
            select(membership.c.id)
            .where(
                membership.c.conversation_id == MessageRow.conversation_id,
                membership.c.user_id == user_id,
            )
            .correlate(MessageRow)
            .exists()
        )

        statement = (
            select(
                MessageRow.id,
                MessageRow.conversation_id,
                MessageRow.user_id_from,
                ConversationMemberRow.user_id.label("user_id_to"),
                MessageRow.subject,
                MessageRow.full_message,
                MessageRow.full_message_html,
                MessageRow.full_message_format,
                MessageRow.small_message,
                MessageRow.time_created,
                sender_block.c.id.label("sender_block"),
                recipient_block.c.id.label("recipient_block"),
            )
            .select_from(MessageRow)
            .join(ConversationRow, ConversationRow.id == MessageRow.conversation_id)
            .join(
                ConversationMemberRow,
                ConversationMemberRow.conversation_id == MessageRow.conversation_id,
            )
            .join(sender, sender.c.user_id == MessageRow.user_id_from)
            .join(recipient, recipient.c.user_id == ConversationMemberRow.user_id)
            .join(
                sender_block,
                (sender_block.c.blocked_user_id == MessageRow.user_id_from)
                & (sender_block.c.user_id == user_id),
                isouter=True,
            )
            .join(
                recipient_block,
                (recipient_block.c.blocked_user_id == ConversationMemberRow.user_id)
                & (recipient_block.c.user_id == user_id),
                isouter=True,
            )
            .join(
                deletion,
                (deletion.c.message_id == MessageRow.id)
                & (deletion.c.user_id == user_id)
                & (deletion.c.action == int(MessageAction.DELETED)),
                isouter=True,
            )
            .where(
                or_(MessageRow.user_id_from == user_id, is_member_of),
                or_(
                    MessageRow.user_id_from != ConversationMemberRow.user_id,
                    ConversationRow.type == int(ConversationType.SELF),
                ),
                sender.c.deleted == 0,
                recipient.c.deleted == 0,
                deletion.c.id.is_(None),
                MessageRow.small_message.icontains(text, autoescape=True),
            )
            # Tie-breaker: instants are kept to the second; the three columns together
            # are unique.
            .order_by(
                MessageRow.time_created.desc(),
                MessageRow.id.desc(),
                ConversationMemberRow.user_id.asc(),
            )
        )

        if limit:
            statement = statement.limit(limit).offset(offset)

        rows = (await self._session.execute(statement)).all()

        return [
            MessageSearchResult(
                message_id=int(row.id),
                conversation_id=int(row.conversation_id),
                user_id_from=int(row.user_id_from),
                user_id_to=int(row.user_id_to),
                subject=row.subject,
                full_message=row.full_message,
                full_message_html=row.full_message_html,
                full_message_format=int(row.full_message_format or 0),
                small_message=row.small_message,
                time_created=int(row.time_created),
                user_from_blocked=row.sender_block is not None,
                user_to_blocked=row.recipient_block is not None,
            )
            for row in rows
        ]

    async def count_unread_in_conversation(self, user_id: int, conversation_id: int) -> int:
        """Count the unread messages of a conversation, excluding those sent by the
        user.
        """
        counts = await self._unread_counts(user_id, [conversation_id])

        return counts.get(conversation_id, 0)

    async def list_countable_conversations(self, user_id: int) -> list[tuple[int, int]]:
        """List the conversations of the user that must be counted, each with its type.
        """
        # A message is visible to the user unless they recorded its deletion; deletion
        # is per user.
        deleted = (
            select(MessageUserActionRow.id)
            .where(
                MessageUserActionRow.message_id == MessageRow.id,
                MessageUserActionRow.user_id == user_id,
                MessageUserActionRow.action == int(MessageAction.DELETED),
            )
            .exists()
        )

        has_visible_message = (
            select(MessageRow.id)
            .where(MessageRow.conversation_id == ConversationRow.id, ~deleted)
            .exists()
        )

        has_any_message = (
            select(MessageRow.id).where(MessageRow.conversation_id == ConversationRow.id).exists()
        )

        statement = (
            select(ConversationRow.id, ConversationRow.type)
            .join(
                ConversationMemberRow,
                (ConversationMemberRow.conversation_id == ConversationRow.id)
                & (ConversationMemberRow.user_id == user_id),
            )
            .where(
                ConversationRow.enabled == int(ConversationState.ENABLED),
                or_(
                    (ConversationRow.type == int(ConversationType.INDIVIDUAL))
                    & has_visible_message,
                    ConversationRow.type == int(ConversationType.GROUP),
                    (ConversationRow.type == int(ConversationType.SELF))
                    & (has_visible_message | ~has_any_message),
                ),
            )
            .order_by(ConversationRow.type.asc(), ConversationRow.id.asc())
        )

        result = await self._session.execute(statement)
        return [(int(cid), int(ctype)) for cid, ctype in result.all()]

    async def delete_by_conversation(self, conversation_id: int) -> None:
        """Remove the messages of a conversation and the actions about them."""
        ids = await self._session.execute(
            select(MessageRow.id).where(MessageRow.conversation_id == conversation_id)
        )
        message_ids = [int(mid) for mid in ids.scalars().all()]

        if message_ids:
            await self._session.execute(
                delete(MessageUserActionRow).where(MessageUserActionRow.message_id.in_(message_ids))
            )
        await self._session.execute(delete(MessageRow).where(MessageRow.conversation_id == conversation_id))


class SqlContactRepository:
    """Repository of contacts, contact requests and blocks."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_contact(self, contact: Contact) -> Contact:
        """Persist a contact relationship, returning the existing one if already
        present.
        """
        existing = await self.get_contact(contact.user_id, contact.contact_id)
        if existing is not None:
            return existing

        row = ContactRow(
            user_id=contact.user_id, contact_id=contact.contact_id, time_created=contact.time_created
        )
        self._session.add(row)
        await self._session.flush()

        contact.id = row.id
        return contact

    async def get_contact(self, user_id: int, contact_id: int) -> Contact | None:
        """Return the contact relationship, in either direction."""
        result = await self._session.execute(
            select(ContactRow).where(
                ((ContactRow.user_id == user_id) & (ContactRow.contact_id == contact_id))
                | ((ContactRow.user_id == contact_id) & (ContactRow.contact_id == user_id))
            )
        )
        row = result.scalars().first()
        if row is None:
            return None
        return Contact(
            id=row.id, user_id=row.user_id, contact_id=row.contact_id, time_created=row.time_created
        )

    async def delete_contact(self, contact_id_row: int) -> None:
        """Remove a contact relationship."""
        await self._session.execute(delete(ContactRow).where(ContactRow.id == contact_id_row))

    async def count_contacts(self, user_id: int) -> int:
        """Count the contacts of the user, in either direction."""
        result = await self._session.execute(
            select(func.count(ContactRow.id)).where(
                (ContactRow.user_id == user_id) | (ContactRow.contact_id == user_id)
            )
        )
        return int(result.scalar_one())

    async def list_contact_ids(self, user_id: int, offset: int = 0, limit: int = 0) -> list[int]:
        """List the users in contact with the given one, most recent first."""
        statement = (
            select(ContactRow)
            .where((ContactRow.user_id == user_id) | (ContactRow.contact_id == user_id))
            .order_by(ContactRow.time_created.desc(), ContactRow.id.asc())
        )
        if limit:
            statement = statement.limit(limit).offset(offset)

        result = await self._session.execute(statement)
        return [
            int(row.contact_id if row.user_id == user_id else row.user_id)
            for row in result.scalars().all()
        ]

    async def add_block(self, block: UserBlock) -> UserBlock:
        """Persist a block, returning the existing one if already present."""
        existing = await self.get_block(block.user_id, block.blocked_user_id)
        if existing is not None:
            return existing

        row = UserBlockRow(
            user_id=block.user_id,
            blocked_user_id=block.blocked_user_id,
            time_created=block.time_created,
        )
        self._session.add(row)
        await self._session.flush()

        block.id = row.id
        return block

    async def get_block(self, user_id: int, blocked_user_id: int) -> UserBlock | None:
        """Return the block applied by the user towards another."""
        result = await self._session.execute(
            select(UserBlockRow).where(
                UserBlockRow.user_id == user_id, UserBlockRow.blocked_user_id == blocked_user_id
            )
        )
        row = result.scalars().first()
        if row is None:
            return None
        return UserBlock(
            id=row.id,
            user_id=row.user_id,
            blocked_user_id=row.blocked_user_id,
            time_created=row.time_created,
        )

    async def delete_block(self, block_row_id: int) -> None:
        """Remove a block."""
        await self._session.execute(delete(UserBlockRow).where(UserBlockRow.id == block_row_id))

    async def list_blocked_user_ids(self, user_id: int) -> list[int]:
        """List the users blocked by the given one."""
        statement = (
            select(UserBlockRow.blocked_user_id)
            .where(UserBlockRow.user_id == user_id)
            .order_by(UserBlockRow.id.asc())
        )

        result = await self._session.execute(statement)
        return [int(uid) for uid in result.scalars().all()]

    async def add_request(self, request: ContactRequest) -> ContactRequest:
        """Persist a contact request, returning the existing one if already present."""
        existing = await self.get_request(request.user_id, request.requested_user_id)
        if existing is not None:
            return existing

        row = ContactRequestRow(
            user_id=request.user_id,
            requested_user_id=request.requested_user_id,
            time_created=request.time_created,
        )
        self._session.add(row)
        await self._session.flush()

        request.id = row.id
        return request

    async def get_request(self, user_id: int, requested_user_id: int) -> ContactRequest | None:
        """Return the contact request in the given direction."""
        result = await self._session.execute(
            select(ContactRequestRow).where(
                ContactRequestRow.user_id == user_id,
                ContactRequestRow.requested_user_id == requested_user_id,
            )
        )
        row = result.scalars().first()
        if row is None:
            return None
        return ContactRequest(
            id=row.id,
            user_id=row.user_id,
            requested_user_id=row.requested_user_id,
            time_created=row.time_created,
        )

    async def list_requests_between(self, user_id: int, requested_user_id: int) -> list[ContactRequest]:
        """Return the contact requests between two users, in both directions."""
        result = await self._session.execute(
            select(ContactRequestRow).where(
                (
                    (ContactRequestRow.user_id == user_id)
                    & (ContactRequestRow.requested_user_id == requested_user_id)
                )
                | (
                    (ContactRequestRow.user_id == requested_user_id)
                    & (ContactRequestRow.requested_user_id == user_id)
                )
            )
        )
        return [
            ContactRequest(
                id=row.id,
                user_id=row.user_id,
                requested_user_id=row.requested_user_id,
                time_created=row.time_created,
            )
            for row in result.scalars().all()
        ]

    async def delete_request(self, request_row_id: int) -> None:
        """Remove a contact request."""
        await self._session.execute(
            delete(ContactRequestRow).where(ContactRequestRow.id == request_row_id)
        )

    async def list_received_request_user_ids(
        self, user_id: int, offset: int = 0, limit: int = 0
    ) -> list[int]:
        """List who asked the given user for contact, oldest request first."""
        blocked = select(UserBlockRow.id).where(
            UserBlockRow.user_id == user_id,
            UserBlockRow.blocked_user_id == ContactRequestRow.user_id,
        )

        statement = (
            select(ContactRequestRow.user_id)
            .where(ContactRequestRow.requested_user_id == user_id, ~blocked.exists())
            .order_by(ContactRequestRow.time_created.asc(), ContactRequestRow.id.asc())
        )
        if limit:
            statement = statement.limit(limit).offset(offset)

        result = await self._session.execute(statement)
        return [int(uid) for uid in result.scalars().all()]

    async def count_received_requests(self, user_id: int) -> int:
        """Count the received requests, excluding those from users the recipient
        blocked.
        """
        blocked = UserBlockRow.__table__.alias("blocked")

        statement = (
            select(func.count(ContactRequestRow.id))
            .select_from(ContactRequestRow)
            .join(
                blocked,
                (blocked.c.user_id == ContactRequestRow.requested_user_id)
                & (blocked.c.blocked_user_id == ContactRequestRow.user_id),
                isouter=True,
            )
            .where(ContactRequestRow.requested_user_id == user_id, blocked.c.id.is_(None))
        )

        result = await self._session.execute(statement)
        return int(result.scalar_one())


class SqlCommandRepository:
    """Register of the executed commands."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def find_response(self, idempotency_key: str, endpoint: str) -> dict | None:
        """Return the answer already produced for that command, if any."""
        result = await self._session.execute(
            select(ProcessedCommandRow.response).where(
                ProcessedCommandRow.idempotency_key == idempotency_key,
                ProcessedCommandRow.endpoint == endpoint,
            )
        )
        row = result.scalars().first()
        return dict(row) if row is not None else None

    async def record(self, idempotency_key: str, endpoint: str, response: dict, now: int) -> None:
        """Record the answer produced for a command."""
        self._session.add(
            ProcessedCommandRow(
                idempotency_key=idempotency_key, endpoint=endpoint, response=response, created_at=now
            )
        )
        await self._session.flush()


__all__ = [
    "SqlCommandRepository",
    "SqlContactRepository",
    "SqlConversationRepository",
    "SqlMessageRepository",
]
