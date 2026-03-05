"""add_unique_reply_per_parent_author

Revision ID: 2b3c4d5e6f7a
Revises: 1a2b3c4d5e6f
Create Date: 2026-03-03 19:15:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "2b3c4d5e6f7a"
down_revision: Union[str, Sequence[str], None] = "1a2b3c4d5e6f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        WITH ranked AS (
            SELECT
                id,
                ROW_NUMBER() OVER (
                    PARTITION BY thread_id, parent_comment_id, author_id
                    ORDER BY created_at ASC, id ASC
                ) AS rn
            FROM comments
            WHERE parent_comment_id IS NOT NULL
              AND status <> 'deleted'
        )
        DELETE FROM comments c
        USING ranked r
        WHERE c.id = r.id
          AND r.rn > 1
        """
    )

    op.create_index(
        "uq_comments_reply_once_per_parent_author",
        "comments",
        ["thread_id", "parent_comment_id", "author_id"],
        unique=True,
        postgresql_where=sa.text(
            "parent_comment_id IS NOT NULL AND status <> 'deleted'"
        ),
    )


def downgrade() -> None:
    op.drop_index("uq_comments_reply_once_per_parent_author", table_name="comments")
