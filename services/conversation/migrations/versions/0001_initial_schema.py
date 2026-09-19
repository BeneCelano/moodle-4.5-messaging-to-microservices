"""Schema of the ConversationService."""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

MESSAGE_PARTITIONS = 8


def upgrade() -> None:
    """Create the schema."""
    op.create_table(
        "conversations",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("type", sa.SmallInteger(), nullable=False, server_default="1"),
        sa.Column("name", sa.String(length=255), nullable=True),
        sa.Column("conversation_hash", sa.String(length=40), nullable=True),
        sa.Column("component", sa.String(length=100), nullable=True),
        sa.Column("item_type", sa.String(length=100), nullable=True),
        sa.Column("item_id", sa.BigInteger(), nullable=True),
        sa.Column("context_id", sa.BigInteger(), nullable=True),
        sa.Column("enabled", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("time_created", sa.BigInteger(), nullable=False),
        sa.Column("time_modified", sa.BigInteger(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_conversations_hash", "conversations", ["conversation_hash"])
    op.create_index("ix_conversations_type", "conversations", ["type"])
    op.create_index(
        "ix_conversations_area", "conversations", ["component", "item_type", "item_id", "context_id"]
    )

    op.create_table(
        "conversation_members",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("conversation_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("time_created", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("conversation_id", "user_id", name="uq_member_conversation_user"),
    )
    op.create_index("ix_members_user", "conversation_members", ["user_id"])

    op.create_table(
        "conversation_actions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("conversation_id", sa.BigInteger(), nullable=False),
        sa.Column("action", sa.SmallInteger(), nullable=False),
        sa.Column("time_created", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "conversation_id", "action", name="uq_action_user_conversation"),
    )

    # The primary key includes the conversation because MySQL requires the partitioning
    # column in every unique key of the table.
    op.create_table(
        "messages",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("conversation_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id_from", sa.BigInteger(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=True),
        sa.Column("full_message", sa.Text(), nullable=True),
        sa.Column("full_message_format", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("full_message_html", sa.Text(), nullable=True),
        sa.Column("small_message", sa.Text(), nullable=True),
        sa.Column("full_message_trust", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("custom_data", sa.Text(), nullable=True),
        sa.Column("time_created", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("id", "conversation_id"),
    )
    op.create_index("ix_messages_conversation_time", "messages", ["conversation_id", "time_created"])
    op.create_index("ix_messages_user_from", "messages", ["user_id_from"])

    op.create_table(
        "message_user_actions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("message_id", sa.BigInteger(), nullable=False),
        sa.Column("action", sa.SmallInteger(), nullable=False),
        sa.Column("time_created", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "message_id", "action", name="uq_action_user_message"),
    )
    op.create_index("ix_message_actions_message", "message_user_actions", ["message_id"])

    op.create_table(
        "contacts",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("contact_id", sa.BigInteger(), nullable=False),
        sa.Column("time_created", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "contact_id", name="uq_contact_pair"),
    )
    op.create_index("ix_contacts_contact", "contacts", ["contact_id"])

    op.create_table(
        "contact_requests",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("requested_user_id", sa.BigInteger(), nullable=False),
        sa.Column("time_created", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "requested_user_id", name="uq_request_pair"),
    )
    op.create_index("ix_requests_requested", "contact_requests", ["requested_user_id"])

    op.create_table(
        "users_blocked",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("blocked_user_id", sa.BigInteger(), nullable=False),
        sa.Column("time_created", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "blocked_user_id", name="uq_block_pair"),
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
        sa.UniqueConstraint("event_id", name="uq_outbox_event_id"),
    )
    op.create_index("ix_outbox_pending", "outbox", ["published_at", "id"])

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

    if op.get_bind().dialect.name == "mysql":
        op.execute(
            f"ALTER TABLE messages PARTITION BY HASH(conversation_id) PARTITIONS {MESSAGE_PARTITIONS}"
        )

    # MySQL requires an index on the referencing column: the unique constraint provides
    # it on `conversation_members`, an explicit index on `conversation_actions`.
    op.create_index("ix_actions_conversation", "conversation_actions", ["conversation_id"])
    op.create_foreign_key(
        "fk_members_conversation",
        source_table="conversation_members",
        referent_table="conversations",
        local_cols=["conversation_id"],
        remote_cols=["id"],
    )
    op.create_foreign_key(
        "fk_actions_conversation",
        source_table="conversation_actions",
        referent_table="conversations",
        local_cols=["conversation_id"],
        remote_cols=["id"],
    )


def downgrade() -> None:
    """Drop the schema."""
    op.drop_constraint("fk_actions_conversation", "conversation_actions", type_="foreignkey")
    op.drop_constraint("fk_members_conversation", "conversation_members", type_="foreignkey")
    op.drop_index("ix_actions_conversation", table_name="conversation_actions")

    if op.get_bind().dialect.name == "mysql":
        op.execute("ALTER TABLE messages REMOVE PARTITIONING")

    for table in (
        "processed_commands",
        "outbox",
        "users_blocked",
        "contact_requests",
        "contacts",
        "message_user_actions",
        "messages",
        "conversation_actions",
        "conversation_members",
        "conversations",
    ):
        op.drop_table(table)
