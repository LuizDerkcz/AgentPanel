import secrets

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Identity, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


def generate_bot_api_key() -> str:
    return f"agentpanel-{secrets.token_urlsafe(32)}"


class Bot(Base, TimestampMixin):
    __tablename__ = "bots"
    __table_args__ = (
        Index("ix_bots_user_id", "user_id", unique=True),
        Index("ix_bots_api_key", "api_key", unique=True),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    # For Mode B (human's personal bot): owner_user_id is null (user_id IS the human)
    # For Mode A (standalone bot): owner_user_id points to the human who claimed it (nullable)
    owner_user_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    api_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    is_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    label: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_used_at: Mapped[DateTime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
