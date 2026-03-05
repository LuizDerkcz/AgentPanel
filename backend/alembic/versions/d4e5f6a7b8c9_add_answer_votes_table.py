"""add_answer_votes_table

Revision ID: d4e5f6a7b8c9
Revises: c2d3e4f5a6b7
Create Date: 2026-02-21 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, Sequence[str], None] = "c2d3e4f5a6b7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "answer_votes",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("comment_id", sa.BigInteger(), nullable=False),
        sa.Column("vote", sa.SmallInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["comment_id"], ["comments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("vote in (1,-1)", name="ck_answer_votes_vote"),
    )

    op.create_index(
        "ix_answer_votes_comment_created",
        "answer_votes",
        ["comment_id", "created_at"],
    )
    op.create_index(
        "ix_answer_votes_user_created",
        "answer_votes",
        ["user_id", "created_at"],
    )
    op.create_index(
        "uq_answer_vote_user_comment",
        "answer_votes",
        ["user_id", "comment_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_answer_vote_user_comment", table_name="answer_votes")
    op.drop_index("ix_answer_votes_user_created", table_name="answer_votes")
    op.drop_index("ix_answer_votes_comment_created", table_name="answer_votes")
    op.drop_table("answer_votes")
