"""add_reveal_results_after_vote_to_prediction_markets

Revision ID: cd34ef56ab78
Revises: bc23de45fa67
Create Date: 2026-03-03 17:30:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "cd34ef56ab78"
down_revision: Union[str, Sequence[str], None] = "bc23de45fa67"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "prediction_markets",
        sa.Column(
            "reveal_results_after_vote",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("prediction_markets", "reveal_results_after_vote")
