"""add_hashed_password_to_users

Revision ID: b1f2a3d4e5f6
Revises: 0241db89e063
Create Date: 2026-02-20 10:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "b1f2a3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "0241db89e063"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users", sa.Column("hashed_password", sa.String(length=255), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("users", "hashed_password")
