from __future__ import annotations

from datetime import datetime

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
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class PredictionMarket(Base, TimestampMixin):
    __tablename__ = "prediction_markets"
    __table_args__ = (
        CheckConstraint(
            "market_type in ('single','multiple')",
            name="ck_prediction_markets_type",
        ),
        CheckConstraint(
            "status in ('open','closed','resolved','cancelled')",
            name="ck_prediction_markets_status",
        ),
        Index("ix_prediction_markets_status_ends", "status", "ends_at"),
        Index("ix_prediction_markets_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    creator_user_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="SET NULL"),
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    market_type: Mapped[str] = mapped_column(String(16), nullable=False)
    is_vote_changeable: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
    )
    reveal_results_after_vote: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PredictionOption(Base, TimestampMixin):
    __tablename__ = "prediction_options"
    __table_args__ = (
        CheckConstraint("sort_order >= 0", name="ck_prediction_options_sort_nonneg"),
        CheckConstraint("vote_count >= 0", name="ck_prediction_options_vote_nonneg"),
        Index("ix_prediction_options_market_sort", "market_id", "sort_order"),
        Index(
            "uq_prediction_options_market_text",
            "market_id",
            "option_text",
            unique=True,
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    market_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("prediction_markets.id", ondelete="CASCADE"),
        nullable=False,
    )
    option_text: Mapped[str] = mapped_column(String(120), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    vote_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class PredictionVote(Base):
    __tablename__ = "prediction_votes"
    __table_args__ = (
        Index("ix_prediction_votes_market_user", "market_id", "user_id"),
        Index("ix_prediction_votes_market_option", "market_id", "option_id"),
        Index(
            "uq_prediction_votes_market_user_option",
            "market_id",
            "user_id",
            "option_id",
            unique=True,
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    market_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("prediction_markets.id", ondelete="CASCADE"),
        nullable=False,
    )
    option_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("prediction_options.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
