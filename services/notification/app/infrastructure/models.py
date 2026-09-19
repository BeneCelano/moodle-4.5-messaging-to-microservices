"""Relational mapping of the schema owned by the service."""

from __future__ import annotations

from sqlalchemy import (
    JSON,
    BigInteger,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base of the models."""


SURROGATE_KEY = BigInteger().with_variant(Integer, "sqlite")
"""Type of the surrogate keys: 64-bit integer in production, plain integer on the in-memory engine
of the test suite, where autoincrement exists only on that type.
"""


class NotificationRow(Base):
    """Row of the notifications table, `notifications` in the monolith."""

    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(SURROGATE_KEY, primary_key=True, autoincrement=True)
    user_id_from: Mapped[int] = mapped_column(BigInteger, nullable=False)
    user_id_to: Mapped[int] = mapped_column(BigInteger, nullable=False)
    subject: Mapped[str | None] = mapped_column(Text)
    full_message: Mapped[str | None] = mapped_column(Text)
    full_message_format: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    full_message_html: Mapped[str | None] = mapped_column(Text)
    small_message: Mapped[str | None] = mapped_column(Text)
    component: Mapped[str | None] = mapped_column(String(100))
    event_type: Mapped[str | None] = mapped_column(String(100))
    context_url: Mapped[str | None] = mapped_column(Text)
    context_url_name: Mapped[str | None] = mapped_column(Text)
    time_read: Mapped[int | None] = mapped_column(BigInteger)
    time_created: Mapped[int] = mapped_column(BigInteger, nullable=False)
    custom_data: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        # The composite index follows the reads (recipient, read state, date); the two
        # simple indexes serve the cleanup.
        Index("ix_notifications_recipient", "user_id_to", "time_read", "time_created"),
        Index("ix_notifications_sender", "user_id_from"),
        Index("ix_notifications_created", "time_created"),
    )


class PopupNotificationRow(Base):
    """Row marking which notifications appear in the popup
    (`message_popup_notifications` in the monolith).
    """

    __tablename__ = "popup_notifications"

    id: Mapped[int] = mapped_column(SURROGATE_KEY, primary_key=True, autoincrement=True)
    notification_id: Mapped[int] = mapped_column(
        SURROGATE_KEY, ForeignKey("notifications.id", ondelete="CASCADE"), nullable=False
    )

    __table_args__ = (
        # Uniqueness as a constraint, not as a read before insert.
        UniqueConstraint("notification_id", name="uq_popup_notification"),
    )


class NotificationTemplateRow(Base):
    """Template with which the service composes the notifications born from a consumed
    event.
    """

    __tablename__ = "notification_templates"

    id: Mapped[int] = mapped_column(SURROGATE_KEY, primary_key=True, autoincrement=True)
    component: Mapped[str] = mapped_column(String(100), nullable=False)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    lang: Mapped[str] = mapped_column(String(30), nullable=False)
    subject: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    # The custom data is a template too (translated button labels); one row per
    # template.
    custom_data: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[int] = mapped_column(BigInteger, nullable=False)

    __table_args__ = (UniqueConstraint("component", "event_type", "lang", name="uq_template_key"),)


class OutboxRow(Base):
    """Row of the outbox."""

    __tablename__ = "outbox"

    id: Mapped[int] = mapped_column(SURROGATE_KEY, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    event_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    routing_key: Mapped[str] = mapped_column(String(120), nullable=False)
    correlation_id: Mapped[str | None] = mapped_column(String(64))
    envelope: Mapped[dict] = mapped_column(JSON, nullable=False)
    occurred_at: Mapped[str] = mapped_column(String(40), nullable=False)
    published_at: Mapped[int | None] = mapped_column(BigInteger)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)

    __table_args__ = (Index("ix_outbox_pending", "published_at", "id"),)


class ProcessedEventRow(Base):
    """Events already consumed (idempotency)."""

    __tablename__ = "processed_events"

    id: Mapped[int] = mapped_column(SURROGATE_KEY, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(36), nullable=False)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    result: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)

    __table_args__ = (UniqueConstraint("event_id", name="uq_processed_event"),)


class ProcessedCommandRow(Base):
    """Answers already produced, indexed by the caller's idempotency key: what `processed_events` is
    for the asynchronous side, for the synchronous one.
    """

    __tablename__ = "processed_commands"

    id: Mapped[int] = mapped_column(SURROGATE_KEY, primary_key=True, autoincrement=True)
    idempotency_key: Mapped[str] = mapped_column(String(80), nullable=False)
    endpoint: Mapped[str] = mapped_column(String(120), nullable=False)
    response: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)

    __table_args__ = (UniqueConstraint("idempotency_key", "endpoint", name="uq_command_key_endpoint"),)
