"""drop_unique_reply_per_parent_author

Revision ID: 3c4d5e6f7a8b
Revises: 2b3c4d5e6f7a
Create Date: 2026-03-03 22:20:00.000000

"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "3c4d5e6f7a8b"
down_revision: Union[str, Sequence[str], None] = "8dd283d4e369"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


INDEX_NAME = "uq_comments_reply_once_per_parent_author"


def upgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {INDEX_NAME}")


def downgrade() -> None:
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_comments_reply_once_per_parent_author
        ON comments (thread_id, parent_comment_id, author_id)
        WHERE parent_comment_id IS NOT NULL AND status <> 'deleted'
        """
    )
