"""add_bots_table

Revision ID: 4d5e6f7a8b9c
Revises: 3c4d5e6f7a8b
Create Date: 2026-03-04 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import func

# revision identifiers, used by Alembic.
revision: str = "4d5e6f7a8b9c"
down_revision: Union[str, Sequence[str], None] = "3c4d5e6f7a8b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "bots",
        sa.Column(
            "id",
            sa.BigInteger,
            sa.Identity(always=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.BigInteger,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("api_key", sa.String(64), nullable=False, unique=True),
        sa.Column(
            "is_enabled",
            sa.Boolean,
            nullable=False,
            server_default="false",
        ),
        sa.Column("label", sa.String(64), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_bots_user_id", "bots", ["user_id"], unique=True)
    op.create_index("ix_bots_api_key", "bots", ["api_key"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_bots_api_key", table_name="bots")
    op.drop_index("ix_bots_user_id", table_name="bots")
    op.drop_table("bots")
