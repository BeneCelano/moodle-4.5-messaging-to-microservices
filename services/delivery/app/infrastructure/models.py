"""Relational mapping of the schema owned by the service."""

from __future__ import annotations

from sqlalchemy import (
    JSON,
    BigInteger,
    Index,
    Integer,
    SmallInteger,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base of the models."""


SURROGATE_KEY = BigInteger().with_variant(Integer, "sqlite")

# Statuses of a delivery. `pending`: to attempt at `next_attempt_at`; `dead`: terminal,
# with a row in `dead_letter`.
STATUS_PENDING = "pending"
STATUS_DELIVERED = "delivered"
STATUS_DEAD = "dead"


class EventRow(Base):
    """The envelope of the accepted event, kept once and not per recipient."""

    __tablename__ = "event"

    event_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    envelope: Mapped[dict] = mapped_column(JSON, nullable=False)
    received_at: Mapped[int] = mapped_column(BigInteger, nullable=False)


class DeliveryRow(Base):
    """A delivery: an event, a recipient, a channel."""

    __tablename__ = "delivery"

    id: Mapped[int] = mapped_column(SURROGATE_KEY, primary_key=True, autoincrement=True)

    # Identifier of the event in the envelope published by the producer.
    event_id: Mapped[str] = mapped_column(String(36), nullable=False)
    recipient_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    channel: Mapped[str] = mapped_column(String(32), nullable=False)

    # Component and provider of the message, as declared in the event.
    component: Mapped[str] = mapped_column(String(100), nullable=False)
    provider: Mapped[str] = mapped_column(String(100), nullable=False)

    status: Mapped[str] = mapped_column(String(16), nullable=False, default=STATUS_PENDING)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # When to retry.
    next_attempt_at: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    # The reason of the last failure, truncated.
    last_error: Mapped[str | None] = mapped_column(String(255))

    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    updated_at: Mapped[int] = mapped_column(BigInteger, nullable=False)

    __table_args__ = (
        # The idempotent receiver, as a unique constraint.
        UniqueConstraint("event_id", "recipient_id", "channel", name="uq_delivery_terna"),
        # Index for the due deliveries.
        Index("ix_delivery_da_tentare", "status", "next_attempt_at"),
        Index("ix_delivery_event", "event_id"),
    )


class DeadLetterRow(Base):
    """A delivery that exhausted the attempts, with its whole envelope: the question this table answers
    is "what did not arrive, and to whom".
    """

    __tablename__ = "dead_letter"

    id: Mapped[int] = mapped_column(SURROGATE_KEY, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(36), nullable=False)
    recipient_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    channel: Mapped[str] = mapped_column(String(32), nullable=False)

    envelope: Mapped[dict] = mapped_column(JSON, nullable=False)
    reason: Mapped[str] = mapped_column(String(255), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)

    __table_args__ = (Index("ix_dead_letter_event", "event_id"),)


class ProcessorRow(Base):
    """An outbound processor and its enabled state: the migration of `message_processors`, seeded when the
    service goes into operation.
    """

    __tablename__ = "processor"

    name: Mapped[str] = mapped_column(String(32), primary_key=True)
    enabled: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1)
    updated_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
