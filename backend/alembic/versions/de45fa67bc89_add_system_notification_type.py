"""add_system_notification_type

Revision ID: de45fa67bc89
Revises: cd34ef56ab78
Create Date: 2026-03-03 15:10:00.000000

"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "de45fa67bc89"
down_revision: Union[str, Sequence[str], None] = "cd34ef56ab78"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint("ck_notifications_type", "notifications", type_="check")
    op.create_check_constraint(
        "ck_notifications_type",
        "notifications",
        "notification_type in ('reply','like','mention','agent_event','system')",
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_notifications_system_broadcast_user
        ON notifications ((payload->>'broadcast_id'), user_id)
        WHERE notification_type = 'system' AND (payload->>'broadcast_id') IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_notifications_system_broadcast_user")
    op.drop_constraint("ck_notifications_type", "notifications", type_="check")
    op.create_check_constraint(
        "ck_notifications_type",
        "notifications",
        "notification_type in ('reply','like','mention','agent_event')",
    )
