"""add_system_settings_visit_base_offset

Revision ID: fb23cd45ef67
Revises: fa12bc34de56
Create Date: 2026-02-28 23:15:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "fb23cd45ef67"
down_revision: Union[str, Sequence[str], None] = "fa12bc34de56"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "system_settings",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "uq_system_settings_key",
        "system_settings",
        ["key"],
        unique=True,
    )

    op.execute(
        sa.text(
            """
            INSERT INTO system_settings (key, value)
            VALUES (
                'visit_base_offset',
                (
                    (
                        SELECT COALESCE(SUM(view_count), 0)
                        FROM threads
                    )
                    +
                    (
                        SELECT COALESCE(SUM(view_count), 0)
                        FROM columns
                    )
                )::text
            )
            """
        )
    )


def downgrade() -> None:
    op.drop_index("uq_system_settings_key", table_name="system_settings")
    op.drop_table("system_settings")
