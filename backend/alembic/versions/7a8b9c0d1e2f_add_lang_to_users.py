"""add_lang_to_users

Revision ID: 7a8b9c0d1e2f
Revises: 6f7a8b9c0d1e
Create Date: 2026-03-04 15:00:00.000000

Note: lang column was added to the DB manually before this migration was created.
Using IF NOT EXISTS to make this idempotent.
"""
from alembic import op

revision = "7a8b9c0d1e2f"
down_revision = "6f7a8b9c0d1e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS lang VARCHAR(8) DEFAULT 'zh'"
    )


def downgrade() -> None:
    op.drop_column("users", "lang")
