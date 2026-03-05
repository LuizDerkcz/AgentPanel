"""add_comment_vote_counters

Revision ID: c2d3e4f5a6b7
Revises: b1f2a3d4e5f6
Create Date: 2026-02-21 10:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "c2d3e4f5a6b7"
down_revision: Union[str, Sequence[str], None] = "b1f2a3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "comments",
        sa.Column(
            "upvote_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "comments",
        sa.Column(
            "downvote_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.create_check_constraint(
        "ck_comments_upvote_count_non_negative",
        "comments",
        "upvote_count >= 0",
    )
    op.create_check_constraint(
        "ck_comments_downvote_count_non_negative",
        "comments",
        "downvote_count >= 0",
    )
    op.alter_column("comments", "upvote_count", server_default=None)
    op.alter_column("comments", "downvote_count", server_default=None)


def downgrade() -> None:
    op.drop_constraint(
        "ck_comments_downvote_count_non_negative", "comments", type_="check"
    )
    op.drop_constraint(
        "ck_comments_upvote_count_non_negative", "comments", type_="check"
    )
    op.drop_column("comments", "downvote_count")
    op.drop_column("comments", "upvote_count")
