"""add_user_follows_table

Revision ID: f8a9b0c1d2e3
Revises: e6f7a8b9c0d1
Create Date: 2026-02-21 18:30:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "f8a9b0c1d2e3"
down_revision: Union[str, Sequence[str], None] = "e6f7a8b9c0d1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_follows",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("follower_user_id", sa.BigInteger(), nullable=False),
        sa.Column("followee_user_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["follower_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["followee_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.CheckConstraint(
            "follower_user_id <> followee_user_id",
            name="ck_user_follows_not_self",
        ),
    )
    op.create_index(
        "ix_user_follows_follower_created",
        "user_follows",
        ["follower_user_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_user_follows_followee_created",
        "user_follows",
        ["followee_user_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "uq_user_follows_pair",
        "user_follows",
        ["follower_user_id", "followee_user_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_user_follows_pair", table_name="user_follows")
    op.drop_index("ix_user_follows_followee_created", table_name="user_follows")
    op.drop_index("ix_user_follows_follower_created", table_name="user_follows")
    op.drop_table("user_follows")
