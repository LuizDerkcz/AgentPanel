"""add_page_view_events_table

Revision ID: fa12bc34de56
Revises: e7f8a9b0c1d2
Create Date: 2026-02-28 17:20:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "fa12bc34de56"
down_revision: Union[str, Sequence[str], None] = "e7f8a9b0c1d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "page_view_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("user_id", sa.BigInteger(), nullable=True),
        sa.Column("visitor_id", sa.String(length=64), nullable=False),
        sa.Column("session_id", sa.String(length=64), nullable=True),
        sa.Column("path", sa.String(length=512), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
    )
    op.create_index(
        "ix_page_view_events_created_at",
        "page_view_events",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        "ix_page_view_events_path_created",
        "page_view_events",
        ["path", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_page_view_events_user_created",
        "page_view_events",
        ["user_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_page_view_events_visitor_created",
        "page_view_events",
        ["visitor_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_page_view_events_visitor_created", table_name="page_view_events")
    op.drop_index("ix_page_view_events_user_created", table_name="page_view_events")
    op.drop_index("ix_page_view_events_path_created", table_name="page_view_events")
    op.drop_index("ix_page_view_events_created_at", table_name="page_view_events")
    op.drop_table("page_view_events")
