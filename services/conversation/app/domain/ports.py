"""Ports of the conversation context: the contracts the domain requires from the
outside, implemented in the infrastructure layer.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .entities import (
    Contact,
    ContactRequest,
    Conversation,
    ConversationSummary,
    Message,
    MessageSearchResult,
    UserBlock,
)
from .events import DomainEvent


@runtime_checkable
class Clock(Protocol):
    """Source of time."""

    def now(self) -> int:
        """Current time in seconds since the epoch."""
        ...


class ConversationRepository(Protocol):
    """Access to the conversation aggregate."""

    async def add(self, conversation: Conversation) -> Conversation: ...

    async def get(self, conversation_id: int) -> Conversation | None: ...

    async def get_by_hash(self, conversation_hash: str) -> Conversation | None: ...

    async def get_by_area(
        self, component: str, item_type: str, item_id: int, context_id: int
    ) -> Conversation | None: ...

    async def find_shared(
        self, user_id: int, other_user_id: int, offset: int, limit: int
    ) -> list[Conversation]: ...

    async def update_state(
        self, conversation_id: int, enabled: int | None, name: str | None, now: int
    ) -> None: ...

    async def delete(self, conversation_id: int) -> None: ...

    async def member_ids(self, conversation_id: int, offset: int = 0, limit: int = 0) -> list[int]: ...

    async def is_member(self, conversation_id: int, user_id: int) -> bool: ...

    async def add_members(self, conversation_id: int, user_ids: list[int], now: int) -> int: ...

    async def remove_members(self, conversation_id: int, user_ids: list[int]) -> int: ...

    async def count_members(self, conversation_id: int) -> int: ...

    async def set_action(self, conversation_id: int, user_id: int, action: int, now: int) -> None: ...

    async def clear_action(self, conversation_id: int, user_id: int, action: int) -> None: ...

    async def has_action(self, conversation_id: int, user_id: int, action: int) -> bool: ...


class MessageRepository(Protocol):
    """Access to messages and to the per-user state about them."""

    async def add(self, message: Message) -> Message: ...

    async def get(self, message_id: int) -> Message | None: ...

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
    ) -> list[dict]: ...

    async def record_action(self, user_id: int, message_id: int, action: int, now: int) -> int: ...

    async def has_action(self, user_id: int, message_id: int, action: int) -> bool: ...

    async def list_unread(self, user_id: int, conversation_id: int | None) -> list[Message]: ...

    async def list_undeleted_in_conversation(self, user_id: int, conversation_id: int) -> list[Message]: ...

    async def count_unread_conversations(self, user_id: int) -> int: ...

    async def count_unread_in_conversation(self, user_id: int, conversation_id: int) -> int: ...

    async def search(
        self, user_id: int, text: str, offset: int = 0, limit: int = 0
    ) -> list[MessageSearchResult]: ...

    async def list_unread_conversations(self, user_id: int) -> list[tuple[int, int]]: ...

    async def list_countable_conversations(self, user_id: int) -> list[tuple[int, int]]: ...

    async def list_conversations(
        self,
        user_id: int,
        offset: int = 0,
        limit: int = 0,
        conversation_type: int | None = None,
        merge_self: bool = False,
        favourite_ids: list[int] | None = None,
        only_favourites: bool | None = None,
    ) -> list[ConversationSummary]: ...

    async def delete_by_conversation(self, conversation_id: int) -> None: ...


class ContactRepository(Protocol):
    """Access to contacts, contact requests and blocks."""

    async def add_contact(self, contact: Contact) -> Contact: ...

    async def get_contact(self, user_id: int, contact_id: int) -> Contact | None: ...

    async def delete_contact(self, contact_id_row: int) -> None: ...

    async def count_contacts(self, user_id: int) -> int: ...

    async def list_contact_ids(self, user_id: int, offset: int = 0, limit: int = 0) -> list[int]: ...

    async def add_block(self, block: UserBlock) -> UserBlock: ...

    async def get_block(self, user_id: int, blocked_user_id: int) -> UserBlock | None: ...

    async def delete_block(self, block_row_id: int) -> None: ...

    async def list_blocked_user_ids(self, user_id: int) -> list[int]: ...

    async def add_request(self, request: ContactRequest) -> ContactRequest: ...

    async def get_request(self, user_id: int, requested_user_id: int) -> ContactRequest | None: ...

    async def list_requests_between(self, user_id: int, requested_user_id: int) -> list[ContactRequest]: ...

    async def delete_request(self, request_row_id: int) -> None: ...

    async def count_received_requests(self, user_id: int) -> int: ...

    async def list_received_request_user_ids(
        self, user_id: int, offset: int = 0, limit: int = 0
    ) -> list[int]: ...


class CommandRepository(Protocol):
    """Register of the commands already executed (idempotency)."""

    async def find_response(self, idempotency_key: str, endpoint: str) -> dict | None: ...

    async def record(self, idempotency_key: str, endpoint: str, response: dict, now: int) -> None: ...


class EventCollector(Protocol):
    """Collects the events to publish at the end of the transaction."""

    def collect(self, event: DomainEvent) -> None: ...


class UnitOfWork(Protocol):
    """Transactional boundary."""

    conversations: ConversationRepository
    messages: MessageRepository
    contacts: ContactRepository
    commands: CommandRepository
    events: EventCollector

    async def __aenter__(self) -> UnitOfWork: ...

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None: ...

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...
