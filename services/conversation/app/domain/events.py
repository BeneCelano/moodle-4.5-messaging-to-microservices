"""Domain events published by the conversation context."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass(slots=True)
class DomainEvent:
    """A fact that happened in the domain, ready for publication."""

    event_type: str
    payload: dict[str, Any]
    event_version: int = 1
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    occurred_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    producer: str = "conversation-service"
    correlation_id: str | None = None

    @property
    def routing_key(self) -> str:
        """Routing key on the broker, derived from the event type."""
        return self.event_type

    def to_envelope(self) -> dict[str, Any]:
        """Serialisable envelope of the event."""
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "event_version": self.event_version,
            "occurred_at": self.occurred_at,
            "producer": self.producer,
            "correlation_id": self.correlation_id,
            "payload": self.payload,
        }


MESSAGE_SENT = "conversation.message.sent"
CONTACT_REQUEST_CREATED = "conversation.contactrequest.created"
CONVERSATION_CREATED = "conversation.conversation.created"


def message_sent(
    *,
    message_id: int,
    conversation_id: int,
    conversation_type: int,
    user_id_from: int,
    recipient_ids: list[int],
    text: str,
    message_format: int,
    time_created: int,
    correlation_id: str | None = None,
) -> DomainEvent:
    """Event announcing that a message was persisted."""
    return DomainEvent(
        event_type=MESSAGE_SENT,
        correlation_id=correlation_id,
        payload={
            "message_id": message_id,
            "conversation_id": conversation_id,
            "conversation_type": conversation_type,
            "user_id_from": user_id_from,
            "recipient_ids": recipient_ids,
            "text": text,
            "message_format": message_format,
            "time_created": time_created,
            "source_component": "moodle",
            "source_event_name": "instantmessage",
        },
    )


def contact_request_created(
    *,
    request_id: int,
    user_id: int,
    requested_user_id: int,
    time_created: int,
    correlation_id: str | None = None,
) -> DomainEvent:
    """Event announcing that a contact request was created."""
    return DomainEvent(
        event_type=CONTACT_REQUEST_CREATED,
        correlation_id=correlation_id,
        payload={
            "request_id": request_id,
            "user_id": user_id,
            "requested_user_id": requested_user_id,
            "time_created": time_created,
            "source_component": "moodle",
            "source_event_name": "messagecontactrequests",
        },
    )


def conversation_created(
    *,
    conversation_id: int,
    conversation_type: int,
    member_ids: list[int],
    time_created: int,
    correlation_id: str | None = None,
) -> DomainEvent:
    """Event announcing that a conversation was created."""
    return DomainEvent(
        event_type=CONVERSATION_CREATED,
        correlation_id=correlation_id,
        payload={
            "conversation_id": conversation_id,
            "conversation_type": conversation_type,
            "member_ids": member_ids,
            "time_created": time_created,
        },
    )
