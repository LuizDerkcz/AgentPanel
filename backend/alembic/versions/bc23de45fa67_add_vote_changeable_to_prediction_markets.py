"""add_vote_changeable_to_prediction_markets

Revision ID: bc23de45fa67
Revises: ab12cd34ef56
Create Date: 2026-03-03 14:10:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "bc23de45fa67"
down_revision: Union[str, Sequence[str], None] = "ab12cd34ef56"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "prediction_markets",
        sa.Column(
            "is_vote_changeable",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )


def downgrade() -> None:
    op.drop_column("prediction_markets", "is_vote_changeable")
