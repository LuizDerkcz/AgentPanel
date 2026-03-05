"""add_bot_usertype_via_bot_owner

Revision ID: 6f7a8b9c0d1e
Revises: 5e6f7a8b9c0d
Create Date: 2026-03-04 14:00:00.000000

Changes:
- users: add 'bot' to ck_users_user_type check constraint
- bots: add owner_user_id (nullable FK to users.id)
- threads: add via_bot boolean (default false)
- comments: add via_bot boolean (default false)
"""
from alembic import op
import sqlalchemy as sa

revision = "6f7a8b9c0d1e"
down_revision = "5e6f7a8b9c0d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Update users check constraint to include 'bot'
    op.drop_constraint("ck_users_user_type", "users", type_="check")
    op.create_check_constraint(
        "ck_users_user_type",
        "users",
        "user_type in ('human','bot','agent','admin')",
    )

    # 2. Add owner_user_id to bots
    op.add_column(
        "bots",
        sa.Column(
            "owner_user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )

    # 3. Add via_bot to threads
    op.add_column(
        "threads",
        sa.Column(
            "via_bot",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )

    # 4. Add via_bot to comments
    op.add_column(
        "comments",
        sa.Column(
            "via_bot",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )


def downgrade() -> None:
    op.drop_column("comments", "via_bot")
    op.drop_column("threads", "via_bot")
    op.drop_column("bots", "owner_user_id")

    op.drop_constraint("ck_users_user_type", "users", type_="check")
    op.create_check_constraint(
        "ck_users_user_type",
        "users",
        "user_type in ('human','agent','admin')",
    )
