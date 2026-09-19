"""Representations of the REST contract, written in the domain vocabulary and not in the
column names of the monolith: the translation is done by the client.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from ..domain.constants import MESSAGE_MAX_LENGTH


class ConversationOut(BaseModel):
    """Conversation returned to the caller."""

    id: int
    type: int
    name: str | None = None
    conversation_hash: str | None = None
    component: str | None = None
    item_type: str | None = None
    item_id: int | None = None
    context_id: int | None = None
    enabled: int
    time_created: int
    time_modified: int | None = None
    member_ids: list[int] = Field(default_factory=list)


class ConversationListOut(BaseModel):
    """List of conversations."""

    conversations: list[ConversationOut]


class CreateConversationIn(BaseModel):
    """Request to create a conversation."""

    type: int
    user_ids: list[int] = Field(min_length=1)
    name: str | None = None
    enabled: int = 1
    component: str | None = None
    item_type: str | None = None
    item_id: int | None = None
    context_id: int | None = None


class UpdateConversationIn(BaseModel):
    """Request to update a conversation."""

    enabled: int | None = None
    name: str | None = None


class MembersIn(BaseModel):
    """List of users to act on."""

    user_ids: list[int] = Field(min_length=1)


class MemberIdsOut(BaseModel):
    """Identifiers of the members of a conversation."""

    member_ids: list[int]


class MembershipOut(BaseModel):
    """Outcome of a membership check."""

    is_member: bool


class CountOut(BaseModel):
    """Result of a count."""

    count: int


class MessageSearchResultOut(BaseModel):
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


class MessageSearchOut(BaseModel):
    """Outcome of a message search."""

    results: list[MessageSearchResultOut]


class LastMessageOut(BaseModel):
    """The last message of a conversation still visible."""

    id: int
    user_id_from: int
    small_message: str | None
    full_message: str | None
    full_message_format: int
    full_message_html: str | None
    full_message_trust: int
    time_created: int


class ConversationSummaryOut(BaseModel):
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
    last_message: LastMessageOut | None


class ConversationSummaryListOut(BaseModel):
    """List of a user's conversations, most recent first."""

    conversations: list[ConversationSummaryOut]


class ConversationTypeOut(BaseModel):
    """A conversation and its type."""

    conversation_id: int
    type: int


class ConversationTypeListOut(BaseModel):
    """List of conversations with their type."""

    conversations: list[ConversationTypeOut]


class UserIdsOut(BaseModel):
    """List of user identifiers."""

    user_ids: list[int]


class SendMessageIn(BaseModel):
    """Request to send a message."""

    user_id_from: int
    text: str = Field(min_length=1, max_length=MESSAGE_MAX_LENGTH)
    format: int = 0


class SendInstantMessageIn(BaseModel):
    """Request to send a message to a user, without knowing the conversation."""

    user_id_from: int
    user_id_to: int
    text: str = Field(min_length=1, max_length=MESSAGE_MAX_LENGTH)
    format: int = 0


class MessageOut(BaseModel):
    """Message returned after persistence."""

    id: int
    conversation_id: int
    user_id_from: int
    text: str | None = None
    time_created: int
    full_message_trust: int = 0
    small_message: str | None = None
    full_message_format: int = 0


class ConversationMessageOut(BaseModel):
    """Message in the form required by the reading of a conversation."""

    id: int
    user_id_from: int
    subject: str | None = None
    full_message: str | None = None
    full_message_html: str | None = None
    full_message_format: int = 0
    full_message_trust: int = 0
    small_message: str | None = None
    time_created: int
    context_id: int | None = None
    time_read: int | None = None


class ConversationMessagesOut(BaseModel):
    """List of the messages of a conversation."""

    messages: list[ConversationMessageOut]


class MarkReadIn(BaseModel):
    """Request to record the reading of a message."""

    user_id: int
    time_read: int | None = None


class MarkAllReadIn(BaseModel):
    """Request to record the reading of every message."""

    conversation_id: int | None = None


class ActionOut(BaseModel):
    """Identifier of the recorded action."""

    action_id: int | None = None


class MarkedMessageOut(BaseModel):
    """Message marked as read."""

    message_id: int
    user_id_from: int
    action_id: int


class MarkedOut(BaseModel):
    """List of the messages marked as read."""

    marked: list[MarkedMessageOut]


class DeletionOut(BaseModel):
    """Deletion recorded for a user."""

    user_id: int
    message_id: int
    action_id: int


class DeletionsOut(BaseModel):
    """List of the recorded deletions."""

    deleted: list[DeletionOut]


class MessageReferenceOut(BaseModel):
    """A message identified by its id, with the fields needed to display it: after the cutover the
    monolith can no longer re-read the body from its own table.
    """

    id: int
    conversation_id: int
    user_id_from: int
    time_created: int
    small_message: str | None = None
    full_message_format: int = 0
    full_message_trust: int = 0


class MuteIn(BaseModel):
    """Request to mute a conversation."""

    user_id: int


class MuteOut(BaseModel):
    """Mute state of a conversation."""

    muted: bool


class ContactIn(BaseModel):
    """Request to create a contact relationship."""

    user_id: int
    contact_id: int


class ContactOut(BaseModel):
    """Contact relationship."""

    id: int
    user_id: int
    contact_id: int
    time_created: int


class ContactExistsOut(BaseModel):
    """Outcome of the check for a contact relationship."""

    is_contact: bool


class BlockIn(BaseModel):
    """Request to block a user."""

    user_id: int
    blocked_user_id: int


class BlockOut(BaseModel):
    """Block applied by a user towards another."""

    id: int
    user_id: int
    blocked_user_id: int
    time_created: int


class BlockExistsOut(BaseModel):
    """Outcome of the check for a block."""

    is_blocked: bool


class ContactRequestIn(BaseModel):
    """Contact request."""

    user_id: int
    requested_user_id: int


class ContactRequestOut(BaseModel):
    """Recorded contact request."""

    id: int
    user_id: int
    requested_user_id: int
    time_created: int


class ContactRequestsOut(BaseModel):
    """List of contact requests."""

    requests: list[ContactRequestOut]


class ContactRequestExistsOut(BaseModel):
    """Outcome of the check for a contact request."""

    exists: bool


class HealthOut(BaseModel):
    """State of the service."""

    status: str
    service: str
    environment: str
    checks: dict[str, str] = Field(default_factory=dict)
