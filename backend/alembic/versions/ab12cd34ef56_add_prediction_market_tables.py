"""add_prediction_market_tables

Revision ID: ab12cd34ef56
Revises: e7f8a9b0c1d2, 1a2b3c4d5e6f
Create Date: 2026-03-03 18:30:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "ab12cd34ef56"
down_revision: Union[str, Sequence[str], None] = ("e7f8a9b0c1d2", "1a2b3c4d5e6f")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "prediction_markets",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("creator_user_id", sa.BigInteger(), nullable=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("market_type", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
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
            "market_type in ('single','multiple')",
            name="ck_prediction_markets_type",
        ),
        sa.CheckConstraint(
            "status in ('open','closed','resolved','cancelled')",
            name="ck_prediction_markets_status",
        ),
        sa.ForeignKeyConstraint(["creator_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_prediction_markets_status_ends",
        "prediction_markets",
        ["status", "ends_at"],
        unique=False,
    )
    op.create_index(
        "ix_prediction_markets_created_at",
        "prediction_markets",
        ["created_at"],
        unique=False,
    )

    op.create_table(
        "prediction_options",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("market_id", sa.BigInteger(), nullable=False),
        sa.Column("option_text", sa.String(length=120), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("vote_count", sa.Integer(), nullable=False, server_default="0"),
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
        sa.CheckConstraint("sort_order >= 0", name="ck_prediction_options_sort_nonneg"),
        sa.CheckConstraint("vote_count >= 0", name="ck_prediction_options_vote_nonneg"),
        sa.ForeignKeyConstraint(
            ["market_id"], ["prediction_markets.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_prediction_options_market_sort",
        "prediction_options",
        ["market_id", "sort_order"],
        unique=False,
    )
    op.create_index(
        "uq_prediction_options_market_text",
        "prediction_options",
        ["market_id", "option_text"],
        unique=True,
    )

    op.create_table(
        "prediction_votes",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("market_id", sa.BigInteger(), nullable=False),
        sa.Column("option_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["market_id"], ["prediction_markets.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["option_id"], ["prediction_options.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_prediction_votes_market_user",
        "prediction_votes",
        ["market_id", "user_id"],
        unique=False,
    )
    op.create_index(
        "ix_prediction_votes_market_option",
        "prediction_votes",
        ["market_id", "option_id"],
        unique=False,
    )
    op.create_index(
        "uq_prediction_votes_market_user_option",
        "prediction_votes",
        ["market_id", "user_id", "option_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "uq_prediction_votes_market_user_option", table_name="prediction_votes"
    )
    op.drop_index("ix_prediction_votes_market_option", table_name="prediction_votes")
    op.drop_index("ix_prediction_votes_market_user", table_name="prediction_votes")
    op.drop_table("prediction_votes")

    op.drop_index("uq_prediction_options_market_text", table_name="prediction_options")
    op.drop_index("ix_prediction_options_market_sort", table_name="prediction_options")
    op.drop_table("prediction_options")

    op.drop_index("ix_prediction_markets_created_at", table_name="prediction_markets")
    op.drop_index("ix_prediction_markets_status_ends", table_name="prediction_markets")
    op.drop_table("prediction_markets")
