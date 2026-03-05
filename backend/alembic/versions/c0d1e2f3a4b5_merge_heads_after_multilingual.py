"""merge heads after multilingual migration

Revision ID: c0d1e2f3a4b5
Revises: a9c1d2e3f4a5, b2c3d4e5f7a8
Create Date: 2026-02-25 17:35:00.000000

"""

from typing import Sequence, Union


# revision identifiers, used by Alembic.
revision: str = "c0d1e2f3a4b5"
down_revision: Union[str, Sequence[str], None] = ("a9c1d2e3f4a5", "b2c3d4e5f7a8")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
