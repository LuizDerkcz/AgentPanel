from datetime import datetime

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
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class DMConversation(Base, TimestampMixin):
    __tablename__ = "dm_conversations"
    __table_args__ = (
        CheckConstraint(
            "type in ('direct','group','system')", name="ck_dm_conversations_type"
        ),
        CheckConstraint(
            "status in ('active','archived','deleted')",
            name="ck_dm_conversations_status",
        ),
        Index("ix_dm_conversations_status_last_msg", "status", "last_message_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    type: Mapped[str] = mapped_column(String(16), nullable=False, default="direct")
    title: Mapped[str | None] = mapped_column(String(120))
    owner_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL")
    )
    last_message_id: Mapped[int | None] = mapped_column(BigInteger)
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    meta: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False, default=dict)


class DMParticipant(Base, TimestampMixin):
    __tablename__ = "dm_participants"
    __table_args__ = (
        CheckConstraint(
            "role in ('owner','admin','member')", name="ck_dm_participants_role"
        ),
        Index("ix_dm_participants_user_updated", "user_id", "updated_at"),
        Index(
            "ix_dm_participants_conv_last_read",
            "conversation_id",
            "last_read_message_id",
        ),
    )

    conversation_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("dm_conversations.id", ondelete="CASCADE"),
        primary_key=True,
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="member")
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    left_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    mute_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_pinned: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_read_message_id: Mapped[int | None] = mapped_column(BigInteger)
    last_read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    extra: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)


class DMMessage(Base, TimestampMixin):
    __tablename__ = "dm_messages"
    __table_args__ = (
        CheckConstraint("msg_type in ('text','system')", name="ck_dm_messages_type"),
        Index("ix_dm_messages_conv_id_desc", "conversation_id", "id"),
        Index("ix_dm_messages_sender_created", "sender_user_id", "created_at"),
        Index(
            "uq_dm_messages_conv_client_msg_id",
            "conversation_id",
            "client_msg_id",
            unique=True,
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("dm_conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    sender_user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    msg_type: Mapped[str] = mapped_column(String(16), nullable=False, default="text")
    body: Mapped[str] = mapped_column(Text, nullable=False)
    body_lang: Mapped[str | None] = mapped_column(String(16))
    reply_to_message_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("dm_messages.id", ondelete="SET NULL"),
    )
    is_edited: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    client_msg_id: Mapped[str | None] = mapped_column(String(64))
    meta: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False, default=dict)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DMPeerPair(Base):
    __tablename__ = "dm_peer_pairs"
    __table_args__ = (
        CheckConstraint("user_low_id < user_high_id", name="ck_dm_peer_pairs_sorted"),
    )

    user_low_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    user_high_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    conversation_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("dm_conversations.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
