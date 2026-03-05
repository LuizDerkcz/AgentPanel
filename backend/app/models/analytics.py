from sqlalchemy import BigInteger, DateTime, ForeignKey, Identity, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class PageViewEvent(Base):
    __tablename__ = "page_view_events"
    __table_args__ = (
        Index("ix_page_view_events_created_at", "created_at"),
        Index("ix_page_view_events_path_created", "path", "created_at"),
        Index("ix_page_view_events_user_created", "user_id", "created_at"),
        Index("ix_page_view_events_visitor_created", "visitor_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    visitor_id: Mapped[str] = mapped_column(String(64), nullable=False)
    session_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    path: Mapped[str] = mapped_column(String(512), nullable=False)
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
