"""baseline_init

Revision ID: 0241db89e063
Revises:
Create Date: 2026-02-18 02:51:49.315944

"""

from typing import Sequence, Union

from alembic import op

from app.models import Base


# revision identifiers, used by Alembic.
revision: str = "0241db89e063"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    Base.metadata.drop_all(bind=bind, checkfirst=True)
