"""add columns and column comments

Revision ID: f1e2d3c4b5a6
Revises: e1f2a3b4c5d6
Create Date: 2026-02-26 22:05:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "f1e2d3c4b5a6"
down_revision: Union[str, Sequence[str], None] = "e1f2a3b4c5d6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "columns",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("author_id", sa.BigInteger(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("abstract", sa.String(length=500), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "source_lang", sa.String(length=16), nullable=False, server_default="und"
        ),
        sa.Column("body_length", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column(
            "status", sa.String(length=16), nullable=False, server_default="published"
        ),
        sa.Column("comment_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("like_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("view_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "last_activity_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
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
        sa.ForeignKeyConstraint(["author_id"], ["users.id"], ondelete="RESTRICT"),
        sa.CheckConstraint(
            "status in ('draft','published','locked','deleted')",
            name="ck_columns_status",
        ),
        sa.CheckConstraint(
            "comment_count >= 0",
            name="ck_columns_comment_count_non_negative",
        ),
        sa.CheckConstraint(
            "like_count >= 0",
            name="ck_columns_like_count_non_negative",
        ),
        sa.CheckConstraint(
            "view_count >= 0",
            name="ck_columns_view_count_non_negative",
        ),
    )
    op.create_index(
        "ix_columns_status_published",
        "columns",
        ["status", "published_at"],
        unique=False,
    )
    op.create_index(
        "ix_columns_author_created",
        "columns",
        ["author_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_columns_last_activity",
        "columns",
        ["last_activity_at"],
        unique=False,
    )

    op.create_table(
        "column_comments",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("column_id", sa.BigInteger(), nullable=False),
        sa.Column("parent_comment_id", sa.BigInteger(), nullable=True),
        sa.Column("root_comment_id", sa.BigInteger(), nullable=True),
        sa.Column("author_id", sa.BigInteger(), nullable=False),
        sa.Column("reply_to_user_id", sa.BigInteger(), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "source_lang", sa.String(length=16), nullable=False, server_default="und"
        ),
        sa.Column("body_length", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("depth", sa.SmallInteger(), nullable=False, server_default="1"),
        sa.Column(
            "status", sa.String(length=16), nullable=False, server_default="visible"
        ),
        sa.Column("like_count", sa.Integer(), nullable=False, server_default="0"),
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
        sa.ForeignKeyConstraint(["column_id"], ["columns.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["parent_comment_id"], ["column_comments.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["root_comment_id"], ["column_comments.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["author_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["reply_to_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.CheckConstraint("depth between 1 and 2", name="ck_column_comments_depth"),
        sa.CheckConstraint(
            "status in ('visible','hidden','deleted')",
            name="ck_column_comments_status",
        ),
        sa.CheckConstraint(
            "like_count >= 0",
            name="ck_column_comments_like_count_non_negative",
        ),
    )
    op.create_index(
        "ix_column_comments_column_created",
        "column_comments",
        ["column_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_column_comments_parent_created",
        "column_comments",
        ["parent_comment_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_column_comments_author_created",
        "column_comments",
        ["author_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_column_comments_author_created", table_name="column_comments")
    op.drop_index("ix_column_comments_parent_created", table_name="column_comments")
    op.drop_index("ix_column_comments_column_created", table_name="column_comments")
    op.drop_table("column_comments")

    op.drop_index("ix_columns_last_activity", table_name="columns")
    op.drop_index("ix_columns_author_created", table_name="columns")
    op.drop_index("ix_columns_status_published", table_name="columns")
    op.drop_table("columns")
