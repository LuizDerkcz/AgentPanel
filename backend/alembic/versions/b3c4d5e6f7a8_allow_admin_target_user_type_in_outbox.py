"""allow admin target_user_type in event_outbox

Revision ID: b3c4d5e6f7a8
Revises: a7b8c9d0e1f2
Create Date: 2026-02-27 11:45:00.000000

"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "b3c4d5e6f7a8"
down_revision: Union[str, Sequence[str], None] = "a7b8c9d0e1f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_event_outbox_target_user_type", "event_outbox", type_="check"
    )
    op.create_check_constraint(
        "ck_event_outbox_target_user_type",
        "event_outbox",
        "target_user_type in ('human','agent','admin') or target_user_type is null",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_event_outbox_target_user_type", "event_outbox", type_="check"
    )
    op.create_check_constraint(
        "ck_event_outbox_target_user_type",
        "event_outbox",
        "target_user_type in ('human','agent') or target_user_type is null",
    )
