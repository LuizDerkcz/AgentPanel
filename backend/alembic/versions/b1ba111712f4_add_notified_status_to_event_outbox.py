"""add notified status to event_outbox

Revision ID: b1ba111712f4
Revises: b3c4d5e6f7a8
Create Date: 2026-02-27 14:10:45.054344

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'b1ba111712f4'
down_revision: Union[str, Sequence[str], None] = 'b3c4d5e6f7a8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint("ck_event_outbox_status", "event_outbox", type_="check")
    op.create_check_constraint(
        "ck_event_outbox_status",
        "event_outbox",
        "status in ('pending','notified','processed','failed')",
    )


def downgrade() -> None:
    op.execute("UPDATE event_outbox SET status = 'pending' WHERE status = 'notified'")
    op.drop_constraint("ck_event_outbox_status", "event_outbox", type_="check")
    op.create_check_constraint(
        "ck_event_outbox_status",
        "event_outbox",
        "status in ('pending','processed','failed')",
    )
