import enum
from urllib.parse import quote_plus

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    String,
    Text,
    event,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class UserType(str, enum.Enum):
    HUMAN = "human"
    BOT = "bot"
    AGENT = "agent"
    ADMIN = "admin"


class UserStatus(str, enum.Enum):
    ACTIVE = "active"
    BLOCKED = "blocked"
    DELETED = "deleted"


class User(Base, TimestampMixin):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "user_type in ('human','bot','agent','admin')", name="ck_users_user_type"
        ),
        CheckConstraint(
            "status in ('active','blocked','deleted')", name="ck_users_status"
        ),
        Index("ix_users_type_status", "user_type", "status"),
        Index("ix_users_created_at_desc", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    user_type: Mapped[str] = mapped_column(
        String(16), nullable=False, default=UserType.HUMAN.value
    )
    username: Mapped[str] = mapped_column(String(150), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(64), nullable=False)
    bio: Mapped[str | None] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(String(254), unique=True)
    hashed_password: Mapped[str | None] = mapped_column(String(255), nullable=True)
    avatar_url: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    is_verified: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=UserStatus.ACTIVE.value
    )
    lang: Mapped[str | None] = mapped_column(
        String(8), nullable=True, server_default="zh"
    )
    karma: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )


def build_default_avatar_url(username: str) -> str:
    safe_seed = quote_plus(username.strip())
    return f"https://api.dicebear.com/9.x/croodles/svg?seed={safe_seed}"


@event.listens_for(User, "before_insert")
def set_default_avatar_before_insert(_, __, target: User) -> None:
    if not target.avatar_url:
        target.avatar_url = build_default_avatar_url(target.username)


@event.listens_for(User, "before_update")
def set_default_avatar_before_update(_, __, target: User) -> None:
    if not target.avatar_url:
        target.avatar_url = build_default_avatar_url(target.username)


class UserFollow(Base):
    __tablename__ = "user_follows"
    __table_args__ = (
        CheckConstraint(
            "follower_user_id <> followee_user_id",
            name="ck_user_follows_not_self",
        ),
        Index("ix_user_follows_follower_created", "follower_user_id", "created_at"),
        Index("ix_user_follows_followee_created", "followee_user_id", "created_at"),
        Index(
            "uq_user_follows_pair",
            "follower_user_id",
            "followee_user_id",
            unique=True,
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    follower_user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    followee_user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
