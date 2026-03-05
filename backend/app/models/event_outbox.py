# Event Outbox model for reliable event processing and delivery.
# This model is designed to store events that need to be processed and delivered to users,


from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class EventOutbox(Base, TimestampMixin):
    __tablename__ = "event_outbox"
    __table_args__ = (
        CheckConstraint(
            "status in ('pending','notified','processed','failed')",
            name="ck_event_outbox_status",
        ),
        CheckConstraint(
            "retry_count >= 0",
            name="ck_event_outbox_retry_count_non_negative",
        ),
        CheckConstraint(
            "action_hint in ('notify_only','consider_reply','must_reply')",
            name="ck_event_outbox_action_hint",
        ),
        CheckConstraint(
            "target_user_type in ('human','agent','admin') or target_user_type is null",
            name="ck_event_outbox_target_user_type",
        ),
        Index(
            "ix_event_outbox_status_available_id",
            "status",
            "available_at",
            "id",
        ),
        Index("ix_event_outbox_dedupe_created", "dedupe_key", "created_at"),
        Index("ix_event_outbox_event_id", "event_id", unique=True),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    event_id: Mapped[str] = mapped_column(String(64), nullable=False)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    thread_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("threads.id", ondelete="CASCADE")
    )
    comment_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("comments.id", ondelete="CASCADE")
    )
    parent_comment_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("comments.id", ondelete="SET NULL")
    )
    actor_user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    target_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL")
    )
    target_user_type: Mapped[str | None] = mapped_column(String(16))
    action_hint: Mapped[str] = mapped_column(String(16), nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(128), nullable=False)
    trace_id: Mapped[str | None] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    available_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    processed_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
