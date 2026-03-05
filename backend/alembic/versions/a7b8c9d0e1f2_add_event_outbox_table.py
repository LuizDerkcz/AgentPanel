"""add event outbox table

Revision ID: a7b8c9d0e1f2
Revises: f1e2d3c4b5a6
Create Date: 2026-02-27 11:10:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "a7b8c9d0e1f2"
down_revision: Union[str, Sequence[str], None] = "f1e2d3c4b5a6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "event_outbox",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("event_id", sa.String(length=64), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("thread_id", sa.BigInteger(), nullable=True),
        sa.Column("comment_id", sa.BigInteger(), nullable=True),
        sa.Column("parent_comment_id", sa.BigInteger(), nullable=True),
        sa.Column("actor_user_id", sa.BigInteger(), nullable=False),
        sa.Column("target_user_id", sa.BigInteger(), nullable=True),
        sa.Column("target_user_type", sa.String(length=16), nullable=True),
        sa.Column("action_hint", sa.String(length=16), nullable=False),
        sa.Column("dedupe_key", sa.String(length=128), nullable=False),
        sa.Column("trace_id", sa.String(length=64), nullable=True),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "status", sa.String(length=16), nullable=False, server_default="pending"
        ),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["thread_id"], ["threads.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["comment_id"], ["comments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["parent_comment_id"], ["comments.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["target_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.CheckConstraint(
            "status in ('pending','processed','failed')",
            name="ck_event_outbox_status",
        ),
        sa.CheckConstraint(
            "retry_count >= 0",
            name="ck_event_outbox_retry_count_non_negative",
        ),
        sa.CheckConstraint(
            "action_hint in ('notify_only','consider_reply','must_reply')",
            name="ck_event_outbox_action_hint",
        ),
        sa.CheckConstraint(
            "target_user_type in ('human','agent') or target_user_type is null",
            name="ck_event_outbox_target_user_type",
        ),
    )
    op.create_index(
        "ix_event_outbox_status_available_id",
        "event_outbox",
        ["status", "available_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_event_outbox_dedupe_created",
        "event_outbox",
        ["dedupe_key", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_event_outbox_event_id", "event_outbox", ["event_id"], unique=True
    )


def downgrade() -> None:
    op.drop_index("ix_event_outbox_event_id", table_name="event_outbox")
    op.drop_index("ix_event_outbox_dedupe_created", table_name="event_outbox")
    op.drop_index("ix_event_outbox_status_available_id", table_name="event_outbox")
    op.drop_table("event_outbox")
