"""Schema of the NotificationService."""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the service schema."""
    op.create_table(
        "notifications",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id_from", sa.BigInteger(), nullable=False),
        sa.Column("user_id_to", sa.BigInteger(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=True),
        sa.Column("full_message", sa.Text(), nullable=True),
        sa.Column("full_message_format", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("full_message_html", sa.Text(), nullable=True),
        sa.Column("small_message", sa.Text(), nullable=True),
        sa.Column("component", sa.String(length=100), nullable=True),
        sa.Column("event_type", sa.String(length=100), nullable=True),
        sa.Column("context_url", sa.Text(), nullable=True),
        sa.Column("context_url_name", sa.Text(), nullable=True),
        sa.Column("time_read", sa.BigInteger(), nullable=True),
        sa.Column("time_created", sa.BigInteger(), nullable=False),
        sa.Column("custom_data", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    # The composite index follows the reads (recipient, read state, date); the two
    # simple ones serve the cleanup.
    op.create_index(
        "ix_notifications_recipient", "notifications", ["user_id_to", "time_read", "time_created"]
    )
    op.create_index("ix_notifications_sender", "notifications", ["user_id_from"])
    op.create_index("ix_notifications_created", "notifications", ["time_created"])

    op.create_table(
        "popup_notifications",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("notification_id", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(["notification_id"], ["notifications.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("notification_id", name="uq_popup_notification"),
    )

    op.create_table(
        "notification_templates",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("component", sa.String(length=100), nullable=False),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("lang", sa.String(length=30), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("custom_data", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("component", "event_type", "lang", name="uq_template_key"),
    )

    op.create_table(
        "outbox",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("event_id", sa.String(length=36), nullable=False),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("event_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("routing_key", sa.String(length=120), nullable=False),
        sa.Column("correlation_id", sa.String(length=64), nullable=True),
        sa.Column("envelope", sa.JSON(), nullable=False),
        sa.Column("occurred_at", sa.String(length=40), nullable=False),
        sa.Column("published_at", sa.BigInteger(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id"),
    )
    op.create_index("ix_outbox_pending", "outbox", ["published_at", "id"])

    op.create_table(
        "processed_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("event_id", sa.String(length=36), nullable=False),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id", name="uq_processed_event"),
    )

    op.create_table(
        "processed_commands",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("idempotency_key", sa.String(length=80), nullable=False),
        sa.Column("endpoint", sa.String(length=120), nullable=False),
        sa.Column("response", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", "endpoint", name="uq_command_key_endpoint"),
    )


def downgrade() -> None:
    """Drop the service schema."""
    op.drop_table("processed_commands")
    op.drop_table("processed_events")
    op.drop_index("ix_outbox_pending", table_name="outbox")
    op.drop_table("outbox")
    op.drop_table("notification_templates")
    op.drop_table("popup_notifications")
    op.drop_index("ix_notifications_created", table_name="notifications")
    op.drop_index("ix_notifications_sender", table_name="notifications")
    op.drop_index("ix_notifications_recipient", table_name="notifications")
    op.drop_table("notifications")
