"""Domain events published and consumed by the notification context."""

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
    producer: str = "notification-service"
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


NOTIFICATION_CREATED = "notification.created"
"""Published by this service."""

CONTACT_REQUEST_CREATED = "conversation.contactrequest.created"
"""Consumed by this service, produced by the ConversationService."""


def notification_created(
    *,
    notification_id: int,
    user_id_from: int,
    user_id_to: int,
    component: str,
    event_type: str,
    subject: str,
    full_message: str,
    full_message_format: int,
    full_message_html: str,
    small_message: str,
    context_url: str | None,
    context_url_name: str | None,
    time_created: int,
    correlation_id: str | None = None,
) -> DomainEvent:
    """Event announcing that a notification was written."""
    return DomainEvent(
        event_type=NOTIFICATION_CREATED,
        correlation_id=correlation_id,
        payload={
            "notification_id": notification_id,
            "recipient_ids": [user_id_to],
            "user_id_from": user_id_from,
            "component": component,
            "provider": event_type,
            "subject": subject,
            "full_message": full_message,
            "full_message_format": full_message_format,
            "full_message_html": full_message_html,
            "small_message": small_message,
            "context_url": context_url,
            "context_url_name": context_url_name,
            "time_created": time_created,
        },
    )
