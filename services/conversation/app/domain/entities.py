"""Entities and value objects of the conversation context."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from .constants import MESSAGE_MAX_LENGTH, ConversationState, ConversationType
from .errors import InvalidConversation, MessageTooLong


def conversation_hash(user_ids: list[int]) -> str:
    """Hash identifying an individual or self conversation."""
    return hashlib.sha1("-".join(str(uid) for uid in sorted(user_ids)).encode()).hexdigest()


@dataclass(slots=True)
class Member:
    """Membership of a user in a conversation."""

    user_id: int
    conversation_id: int | None = None
    time_created: int = 0
    id: int | None = None


@dataclass(slots=True)
class Conversation:
    """Conversation aggregate."""

    type: ConversationType
    time_created: int
    id: int | None = None
    name: str | None = None
    conversation_hash: str | None = None
    component: str | None = None
    item_type: str | None = None
    item_id: int | None = None
    context_id: int | None = None
    enabled: int = int(ConversationState.ENABLED)
    time_modified: int | None = None
    members: list[Member] = field(default_factory=list)

    @classmethod
    def create(
        cls,
        conversation_type: ConversationType,
        user_ids: list[int],
        now: int,
        name: str | None = None,
        enabled: int = int(ConversationState.ENABLED),
        component: str | None = None,
        item_type: str | None = None,
        item_id: int | None = None,
        context_id: int | None = None,
    ) -> Conversation:
        """Build a valid conversation from its initial members."""
        cls._assert_composition_is_valid(conversation_type, user_ids)

        needs_hash = conversation_type in (ConversationType.INDIVIDUAL, ConversationType.SELF)

        return cls(
            type=conversation_type,
            name=name,
            conversation_hash=conversation_hash(user_ids) if needs_hash else None,
            component=component,
            item_type=item_type,
            item_id=item_id,
            context_id=context_id,
            enabled=enabled,
            time_created=now,
            time_modified=now,
            members=[Member(user_id=uid, time_created=now) for uid in user_ids],
        )

    @staticmethod
    def _assert_composition_is_valid(conversation_type: ConversationType, user_ids: list[int]) -> None:
        """Check the invariants on the initial members."""
        if not user_ids:
            raise InvalidConversation("A conversation needs at least one member.")

        if conversation_type is ConversationType.INDIVIDUAL:
            if len(user_ids) > 2:
                raise InvalidConversation("An individual conversation can not have more than two users.")
            if len(user_ids) == 2 and user_ids[0] == user_ids[1]:
                raise InvalidConversation(
                    "Trying to create an individual conversation instead of a self conversation."
                )
        elif conversation_type is ConversationType.SELF and len(user_ids) != 1:
            raise InvalidConversation("A self conversation can not have more than one user.")

    @property
    def is_enabled(self) -> bool:
        """Whether the conversation is enabled."""
        return int(self.enabled) == int(ConversationState.ENABLED)

    def member_ids(self) -> list[int]:
        """Identifiers of the members."""
        return [member.user_id for member in self.members]


@dataclass(slots=True)
class Message:
    """Message belonging to a conversation."""

    conversation_id: int
    user_id_from: int
    text: str
    time_created: int
    id: int | None = None
    subject: str | None = None
    full_message: str | None = None
    full_message_format: int = 0
    full_message_html: str | None = None
    small_message: str | None = None
    full_message_trust: int = 0
    custom_data: str | None = None

    @classmethod
    def compose(
        cls,
        conversation_id: int,
        user_id_from: int,
        text: str,
        message_format: int,
        now: int,
        html_format: int,
        custom_data: str | None = None,
    ) -> Message:
        """Build a valid message."""
        if len(text) > MESSAGE_MAX_LENGTH:
            raise MessageTooLong(f"The message is longer than the maximum allowed ({MESSAGE_MAX_LENGTH}).")

        is_html = message_format == html_format

        return cls(
            conversation_id=conversation_id,
            user_id_from=user_id_from,
            text=text,
            full_message=text,
            full_message_html=text if is_html else "",
            full_message_format=message_format,
            small_message=text,
            time_created=now,
            custom_data=custom_data,
        )


@dataclass(slots=True)
class ConversationSummary:
    """A conversation as it appears in a user's list."""

    id: int
    type: int
    name: str | None
    component: str | None
    item_type: str | None
    item_id: int | None
    context_id: int | None
    is_muted: bool
    member_count: int
    unread_count: int
    member_ids: list[int]
    last_message: Message | None


@dataclass(slots=True)
class MessageSearchResult:
    """A message found by the search, with the user facing it."""

    message_id: int
    conversation_id: int
    user_id_from: int
    user_id_to: int
    subject: str | None
    full_message: str | None
    full_message_html: str | None
    full_message_format: int
    small_message: str | None
    time_created: int
    user_from_blocked: bool
    user_to_blocked: bool


@dataclass(slots=True)
class Contact:
    """Contact relationship between two users."""

    user_id: int
    contact_id: int
    time_created: int
    id: int | None = None


@dataclass(slots=True)
class ContactRequest:
    """Contact request awaiting an answer."""

    user_id: int
    requested_user_id: int
    time_created: int
    id: int | None = None


@dataclass(slots=True)
class UserBlock:
    """Block applied by a user towards another."""

    user_id: int
    blocked_user_id: int
    time_created: int
    id: int | None = None
