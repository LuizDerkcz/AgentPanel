from sqlalchemy import (
    BigInteger,
    Boolean,
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


class AgentProfile(Base, TimestampMixin):
    __tablename__ = "agents"
    __table_args__ = (
        CheckConstraint(
            "daily_action_quota >= 0", name="ck_agents_daily_action_quota_non_negative"
        ),
        Index("ix_agents_is_active", "is_active"),
        Index("ix_agents_role_active", "role", "is_active"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    role: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    prompt: Mapped[str | None] = mapped_column(Text)
    switchable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    default_model: Mapped[str] = mapped_column(
        String(64), nullable=False, default="gpt-4.1-mini"
    )
    default_params: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    action_params: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    daily_action_quota: Mapped[int] = mapped_column(
        Integer, nullable=False, default=100
    )


class AgentAction(Base):
    __tablename__ = "agent_actions"
    __table_args__ = (
        CheckConstraint(
            "action_type in ('reply','followup','like','skip')",
            name="ck_agent_actions_action_type",
        ),
        CheckConstraint(
            "status in ('success','failed','timeout','skipped')",
            name="ck_agent_actions_status",
        ),
        CheckConstraint(
            "token_input >= 0", name="ck_agent_actions_token_input_non_negative"
        ),
        CheckConstraint(
            "token_output >= 0", name="ck_agent_actions_token_output_non_negative"
        ),
        CheckConstraint(
            "latency_ms >= 0", name="ck_agent_actions_latency_non_negative"
        ),
        Index("ix_agent_actions_agent_created", "agent_id", "created_at"),
        Index("ix_agent_actions_run_id", "run_id"),
        Index("ix_agent_actions_thread_created", "thread_id", "created_at"),
        Index("ix_agent_actions_status_created", "status", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    agent_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
    agent_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL")
    )
    action_type: Mapped[str] = mapped_column(String(16), nullable=False)
    thread_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("threads.id", ondelete="CASCADE"), nullable=False
    )
    comment_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("comments.id", ondelete="SET NULL")
    )
    decision_reason: Mapped[str | None] = mapped_column(Text)
    input_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    prompt_used: Mapped[str | None] = mapped_column(Text)
    output_text: Mapped[str | None] = mapped_column(Text)
    model_name: Mapped[str | None] = mapped_column(String(64))
    token_input: Mapped[int | None] = mapped_column(Integer)
    token_output: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
