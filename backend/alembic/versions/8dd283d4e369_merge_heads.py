"""merge heads

Revision ID: 8dd283d4e369
Revises: 2b3c4d5e6f7a, de45fa67bc89
Create Date: 2026-03-03 19:08:27.956215

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8dd283d4e369'
down_revision: Union[str, Sequence[str], None] = ('2b3c4d5e6f7a', 'de45fa67bc89')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
