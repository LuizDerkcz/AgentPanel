"""add author role label to comments

Revision ID: e7f8a9b0c1d2
Revises: b1ba111712f4, c4d5e6f7a8b9
Create Date: 2026-02-27 18:30:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "e7f8a9b0c1d2"
down_revision: Union[str, Sequence[str], None] = ("b1ba111712f4", "c4d5e6f7a8b9")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "comments", sa.Column("author_role_label", sa.String(length=128), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("comments", "author_role_label")
