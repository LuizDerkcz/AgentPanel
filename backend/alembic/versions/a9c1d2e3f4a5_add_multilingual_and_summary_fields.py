"""add multilingual and summary fields

Revision ID: a9c1d2e3f4a5
Revises: f8a9b0c1d2e3
Create Date: 2026-02-25 16:30:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "a9c1d2e3f4a5"
down_revision: Union[str, Sequence[str], None] = "f8a9b0c1d2e3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "threads",
        sa.Column(
            "source_lang",
            sa.String(length=16),
            nullable=False,
            server_default="und",
        ),
    )
    op.add_column(
        "threads",
        sa.Column(
            "body_length",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column("threads", sa.Column("summary", sa.Text(), nullable=True))

    op.add_column(
        "comments",
        sa.Column(
            "source_lang",
            sa.String(length=16),
            nullable=False,
            server_default="und",
        ),
    )
    op.add_column(
        "comments",
        sa.Column(
            "body_length",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column("comments", sa.Column("answer_summary", sa.Text(), nullable=True))

    op.execute("UPDATE threads SET body_length = char_length(coalesce(body, ''))")
    op.execute("UPDATE comments SET body_length = char_length(coalesce(body, ''))")

    op.create_table(
        "content_translations",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("target_type", sa.String(length=16), nullable=False),
        sa.Column("target_id", sa.BigInteger(), nullable=False),
        sa.Column("field_name", sa.String(length=32), nullable=False),
        sa.Column("lang", sa.String(length=16), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column(
            "source",
            sa.String(length=16),
            nullable=False,
            server_default="manual",
        ),
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
        sa.CheckConstraint(
            "target_type in ('thread','comment')",
            name="ck_content_translations_target_type",
        ),
        sa.CheckConstraint(
            "field_name in ('title','abstract','body','summary','answer_summary')",
            name="ck_content_translations_field_name",
        ),
    )

    op.create_index(
        "uq_content_translation_target_field_lang",
        "content_translations",
        ["target_type", "target_id", "field_name", "lang"],
        unique=True,
    )
    op.create_index(
        "ix_content_translation_target",
        "content_translations",
        ["target_type", "target_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_content_translation_target", table_name="content_translations")
    op.drop_index(
        "uq_content_translation_target_field_lang",
        table_name="content_translations",
    )
    op.drop_table("content_translations")

    op.drop_column("comments", "answer_summary")
    op.drop_column("comments", "body_length")
    op.drop_column("comments", "source_lang")

    op.drop_column("threads", "summary")
    op.drop_column("threads", "body_length")
    op.drop_column("threads", "source_lang")
