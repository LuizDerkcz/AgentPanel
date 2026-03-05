"""add_thread_debate_fields

Revision ID: fc34de56fa78
Revises: fb23cd45ef67
Create Date: 2026-03-01 09:30:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "fc34de56fa78"
down_revision: Union[str, Sequence[str], None] = "fb23cd45ef67"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("threads", sa.Column("debate_summary", sa.Text(), nullable=True))
    op.add_column("threads", sa.Column("debate_score", sa.Integer(), nullable=True))
    op.add_column(
        "threads",
        sa.Column(
            "debate_context_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "threads",
        sa.Column("debate_updated_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("threads", "debate_updated_at")
    op.drop_column("threads", "debate_context_snapshot")
    op.drop_column("threads", "debate_score")
    op.drop_column("threads", "debate_summary")
