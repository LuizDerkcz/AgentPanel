"""add_thread_view_count

Revision ID: e6f7a8b9c0d1
Revises: d4e5f6a7b8c9
Create Date: 2026-02-21 14:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "e6f7a8b9c0d1"
down_revision: Union[str, Sequence[str], None] = "d4e5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "threads",
        sa.Column(
            "view_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.create_check_constraint(
        "ck_threads_view_count_non_negative",
        "threads",
        "view_count >= 0",
    )
    op.alter_column("threads", "view_count", server_default=None)


def downgrade() -> None:
    op.drop_constraint("ck_threads_view_count_non_negative", "threads", type_="check")
    op.drop_column("threads", "view_count")
