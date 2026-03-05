"""add_dm_tables

Revision ID: 1a2b3c4d5e6f
Revises: fc34de56fa78
Create Date: 2026-03-02 19:30:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "1a2b3c4d5e6f"
down_revision: Union[str, Sequence[str], None] = "fc34de56fa78"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "dm_conversations",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("type", sa.String(length=16), nullable=False),
        sa.Column("title", sa.String(length=120), nullable=True),
        sa.Column("owner_user_id", sa.BigInteger(), nullable=True),
        sa.Column("last_message_id", sa.BigInteger(), nullable=True),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "type in ('direct','group','system')", name="ck_dm_conversations_type"
        ),
        sa.CheckConstraint(
            "status in ('active','archived','deleted')",
            name="ck_dm_conversations_status",
        ),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_dm_conversations_status_last_msg",
        "dm_conversations",
        ["status", "last_message_at"],
        unique=False,
    )

    op.create_table(
        "dm_participants",
        sa.Column("conversation_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column(
            "joined_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("left_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("mute_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "is_pinned", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("last_read_message_id", sa.BigInteger(), nullable=True),
        sa.Column("last_read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "extra",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "role in ('owner','admin','member')", name="ck_dm_participants_role"
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["dm_conversations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("conversation_id", "user_id"),
    )
    op.create_index(
        "ix_dm_participants_user_updated",
        "dm_participants",
        ["user_id", "updated_at"],
        unique=False,
    )
    op.create_index(
        "ix_dm_participants_conv_last_read",
        "dm_participants",
        ["conversation_id", "last_read_message_id"],
        unique=False,
    )

    op.create_table(
        "dm_messages",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("conversation_id", sa.BigInteger(), nullable=False),
        sa.Column("sender_user_id", sa.BigInteger(), nullable=False),
        sa.Column("msg_type", sa.String(length=16), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("body_lang", sa.String(length=16), nullable=True),
        sa.Column("reply_to_message_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "is_edited", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column(
            "is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("client_msg_id", sa.String(length=64), nullable=True),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("msg_type in ('text','system')", name="ck_dm_messages_type"),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["dm_conversations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["sender_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["reply_to_message_id"], ["dm_messages.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_dm_messages_conv_id_desc",
        "dm_messages",
        ["conversation_id", "id"],
        unique=False,
    )
    op.create_index(
        "ix_dm_messages_sender_created",
        "dm_messages",
        ["sender_user_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "uq_dm_messages_conv_client_msg_id",
        "dm_messages",
        ["conversation_id", "client_msg_id"],
        unique=True,
    )

    op.create_foreign_key(
        "fk_dm_conversations_last_message",
        "dm_conversations",
        "dm_messages",
        ["last_message_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_table(
        "dm_peer_pairs",
        sa.Column("user_low_id", sa.BigInteger(), nullable=False),
        sa.Column("user_high_id", sa.BigInteger(), nullable=False),
        sa.Column("conversation_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "user_low_id < user_high_id", name="ck_dm_peer_pairs_sorted"
        ),
        sa.ForeignKeyConstraint(["user_low_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_high_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["dm_conversations.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("user_low_id", "user_high_id"),
        sa.UniqueConstraint("conversation_id"),
    )


def downgrade() -> None:
    op.drop_table("dm_peer_pairs")
    op.drop_constraint(
        "fk_dm_conversations_last_message", "dm_conversations", type_="foreignkey"
    )
    op.drop_index("uq_dm_messages_conv_client_msg_id", table_name="dm_messages")
    op.drop_index("ix_dm_messages_sender_created", table_name="dm_messages")
    op.drop_index("ix_dm_messages_conv_id_desc", table_name="dm_messages")
    op.drop_table("dm_messages")
    op.drop_index("ix_dm_participants_conv_last_read", table_name="dm_participants")
    op.drop_index("ix_dm_participants_user_updated", table_name="dm_participants")
    op.drop_table("dm_participants")
    op.drop_index("ix_dm_conversations_status_last_msg", table_name="dm_conversations")
    op.drop_table("dm_conversations")
