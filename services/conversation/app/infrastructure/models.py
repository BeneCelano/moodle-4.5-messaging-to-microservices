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


class ConversationRow(Base):
    """Row of the conversations table."""

    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(SURROGATE_KEY, primary_key=True, autoincrement=True)
    type: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1)
    name: Mapped[str | None] = mapped_column(String(255))
    conversation_hash: Mapped[str | None] = mapped_column(String(40))
    component: Mapped[str | None] = mapped_column(String(100))
    item_type: Mapped[str | None] = mapped_column(String(100))
    item_id: Mapped[int | None] = mapped_column(BigInteger)
    context_id: Mapped[int | None] = mapped_column(BigInteger)
    enabled: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    time_created: Mapped[int] = mapped_column(BigInteger, nullable=False)
    time_modified: Mapped[int | None] = mapped_column(BigInteger)

    __table_args__ = (
        Index("ix_conversations_hash", "conversation_hash"),
        Index("ix_conversations_type", "type"),
        Index("ix_conversations_area", "component", "item_type", "item_id", "context_id"),
    )


class ConversationMemberRow(Base):
    """Row of the conversation members table."""

    __tablename__ = "conversation_members"

    id: Mapped[int] = mapped_column(SURROGATE_KEY, primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("conversations.id", name="fk_members_conversation"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    time_created: Mapped[int] = mapped_column(BigInteger, nullable=False)

    __table_args__ = (
        UniqueConstraint("conversation_id", "user_id", name="uq_member_conversation_user"),
        Index("ix_members_user", "user_id"),
    )


class ConversationActionRow(Base):
    """Row of the per-user actions on a whole conversation."""

    __tablename__ = "conversation_actions"

    id: Mapped[int] = mapped_column(SURROGATE_KEY, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    conversation_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("conversations.id", name="fk_actions_conversation"), nullable=False
    )
    action: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    time_created: Mapped[int] = mapped_column(BigInteger, nullable=False)

    __table_args__ = (
        UniqueConstraint("user_id", "conversation_id", "action", name="uq_action_user_conversation"),
    )


class MessageRow(Base):
    """Row of the messages table."""

    __tablename__ = "messages"

    # The mapper key is the id alone; the physical key in production also includes the
    # conversation (partitioning).
    id: Mapped[int] = mapped_column(SURROGATE_KEY, primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    user_id_from: Mapped[int] = mapped_column(BigInteger, nullable=False)
    subject: Mapped[str | None] = mapped_column(Text)
    full_message: Mapped[str | None] = mapped_column(Text)
    full_message_format: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    full_message_html: Mapped[str | None] = mapped_column(Text)
    small_message: Mapped[str | None] = mapped_column(Text)
    full_message_trust: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    custom_data: Mapped[str | None] = mapped_column(Text)
    time_created: Mapped[int] = mapped_column(BigInteger, nullable=False)

    __table_args__ = (
        Index("ix_messages_conversation_time", "conversation_id", "time_created"),
        Index("ix_messages_user_from", "user_id_from"),
    )


class MessageUserActionRow(Base):
    """Row of the per-user actions on a single message."""

    __tablename__ = "message_user_actions"

    id: Mapped[int] = mapped_column(SURROGATE_KEY, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    action: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    time_created: Mapped[int] = mapped_column(BigInteger, nullable=False)

    __table_args__ = (
        UniqueConstraint("user_id", "message_id", "action", name="uq_action_user_message"),
        Index("ix_message_actions_message", "message_id"),
    )


class ContactRow(Base):
    """Row of the contacts table."""

    __tablename__ = "contacts"

    id: Mapped[int] = mapped_column(SURROGATE_KEY, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    contact_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    time_created: Mapped[int] = mapped_column(BigInteger, nullable=False)

    __table_args__ = (
        UniqueConstraint("user_id", "contact_id", name="uq_contact_pair"),
        Index("ix_contacts_contact", "contact_id"),
    )


class ContactRequestRow(Base):
    """Row of the contact requests table."""

    __tablename__ = "contact_requests"

    id: Mapped[int] = mapped_column(SURROGATE_KEY, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    requested_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    time_created: Mapped[int] = mapped_column(BigInteger, nullable=False)

    __table_args__ = (
        UniqueConstraint("user_id", "requested_user_id", name="uq_request_pair"),
        Index("ix_requests_requested", "requested_user_id"),
    )


class UserBlockRow(Base):
    """Row of the blocks between users."""

    __tablename__ = "users_blocked"

    id: Mapped[int] = mapped_column(SURROGATE_KEY, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    blocked_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    time_created: Mapped[int] = mapped_column(BigInteger, nullable=False)

    __table_args__ = (UniqueConstraint("user_id", "blocked_user_id", name="uq_block_pair"),)


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


class ProcessedCommandRow(Base):
    """Row of the executed commands."""

    __tablename__ = "processed_commands"

    id: Mapped[int] = mapped_column(SURROGATE_KEY, primary_key=True, autoincrement=True)
    idempotency_key: Mapped[str] = mapped_column(String(80), nullable=False)
    endpoint: Mapped[str] = mapped_column(String(120), nullable=False)
    response: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)

    __table_args__ = (
        UniqueConstraint("idempotency_key", "endpoint", name="uq_command_key_endpoint"),
    )


class UserViewRow(Base):
    """Read-only local projection of the user data needed for presentation."""

    __tablename__ = "user_view"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    full_name: Mapped[str | None] = mapped_column(String(255))
    picture_url: Mapped[str | None] = mapped_column(String(512))
    deleted: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    updated_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
