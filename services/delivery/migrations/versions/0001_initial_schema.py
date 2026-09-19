"""Schema of the DeliveryGateway."""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the schema."""
    op.create_table(
        "event",
        sa.Column("event_id", sa.String(length=36), nullable=False),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("envelope", sa.JSON(), nullable=False),
        sa.Column("received_at", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("event_id"),
    )

    op.create_table(
        "delivery",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("event_id", sa.String(length=36), nullable=False),
        sa.Column("recipient_id", sa.BigInteger(), nullable=False),
        sa.Column("channel", sa.String(length=32), nullable=False),
        sa.Column("component", sa.String(length=100), nullable=False),
        sa.Column("provider", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        # The idempotent receiver, as a unique constraint.
        sa.UniqueConstraint("event_id", "recipient_id", "channel", name="uq_delivery_terna"),
    )
    op.create_index("ix_delivery_da_tentare", "delivery", ["status", "next_attempt_at"])
    op.create_index("ix_delivery_event", "delivery", ["event_id"])

    op.create_table(
        "dead_letter",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("event_id", sa.String(length=36), nullable=False),
        sa.Column("recipient_id", sa.BigInteger(), nullable=False),
        sa.Column("channel", sa.String(length=32), nullable=False),
        sa.Column("envelope", sa.JSON(), nullable=False),
        sa.Column("reason", sa.String(length=255), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_dead_letter_event", "dead_letter", ["event_id"])

    op.create_table(
        "processor",
        sa.Column("name", sa.String(length=32), nullable=False),
        sa.Column("enabled", sa.SmallInteger(), nullable=False, server_default="1"),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("name"),
    )


def downgrade() -> None:
    """Drop the schema."""
    op.drop_table("processor")
    op.drop_index("ix_dead_letter_event", table_name="dead_letter")
    op.drop_table("dead_letter")
    op.drop_index("ix_delivery_event", table_name="delivery")
    op.drop_index("ix_delivery_da_tentare", table_name="delivery")
    op.drop_table("delivery")
    op.drop_table("event")
