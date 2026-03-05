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
    SmallInteger,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class Category(Base, TimestampMixin):
    __tablename__ = "categories"
    __table_args__ = (
        Index("ix_categories_sort_order_id", "sort_order", "id"),
        Index("ix_categories_active_sort", "is_active", "sort_order"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class Thread(Base, TimestampMixin):
    __tablename__ = "threads"
    __table_args__ = (
        CheckConstraint(
            "status in ('draft','published','locked','deleted')",
            name="ck_threads_status",
        ),
        CheckConstraint("reply_count >= 0", name="ck_threads_reply_count_non_negative"),
        CheckConstraint("like_count >= 0", name="ck_threads_like_count_non_negative"),
        CheckConstraint("view_count >= 0", name="ck_threads_view_count_non_negative"),
        Index(
            "ix_threads_category_status_pinned_activity",
            "category_id",
            "status",
            "is_pinned",
            "last_activity_at",
        ),
        Index("ix_threads_author_created", "author_id", "created_at"),
        Index("ix_threads_last_activity", "last_activity_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    category_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("categories.id", ondelete="RESTRICT"), nullable=False
    )
    author_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    abstract: Mapped[str | None] = mapped_column(String(500))
    body: Mapped[str] = mapped_column(Text, nullable=False)
    source_lang: Mapped[str] = mapped_column(String(16), nullable=False, default="und")
    body_length: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    summary: Mapped[str | None] = mapped_column(Text)
    debate_summary: Mapped[str | None] = mapped_column(Text)
    debate_score: Mapped[int | None] = mapped_column(Integer)
    debate_context_snapshot: Mapped[dict | None] = mapped_column(JSONB)
    debate_updated_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="published")
    is_pinned: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    pinned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reply_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    like_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    view_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_activity_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    via_bot: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )


class Column(Base, TimestampMixin):
    __tablename__ = "columns"
    __table_args__ = (
        CheckConstraint(
            "status in ('draft','published','locked','deleted')",
            name="ck_columns_status",
        ),
        CheckConstraint(
            "comment_count >= 0", name="ck_columns_comment_count_non_negative"
        ),
        CheckConstraint("like_count >= 0", name="ck_columns_like_count_non_negative"),
        CheckConstraint("view_count >= 0", name="ck_columns_view_count_non_negative"),
        Index("ix_columns_status_published", "status", "published_at"),
        Index("ix_columns_author_created", "author_id", "created_at"),
        Index("ix_columns_last_activity", "last_activity_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    author_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    abstract: Mapped[str | None] = mapped_column(String(500))
    body: Mapped[str] = mapped_column(Text, nullable=False)
    source_lang: Mapped[str] = mapped_column(String(16), nullable=False, default="und")
    body_length: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    summary: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="published")
    comment_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    like_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    view_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    published_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True))
    last_activity_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ColumnComment(Base, TimestampMixin):
    __tablename__ = "column_comments"
    __table_args__ = (
        CheckConstraint("depth between 1 and 3", name="ck_column_comments_depth"),
        CheckConstraint(
            "status in ('visible','hidden','deleted')",
            name="ck_column_comments_status",
        ),
        CheckConstraint(
            "like_count >= 0", name="ck_column_comments_like_count_non_negative"
        ),
        Index("ix_column_comments_column_created", "column_id", "created_at"),
        Index(
            "ix_column_comments_parent_created",
            "parent_comment_id",
            "created_at",
        ),
        Index("ix_column_comments_author_created", "author_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    column_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("columns.id", ondelete="CASCADE"), nullable=False
    )
    parent_comment_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("column_comments.id", ondelete="CASCADE"),
    )
    root_comment_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("column_comments.id", ondelete="CASCADE"),
    )
    author_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    reply_to_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT")
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    source_lang: Mapped[str] = mapped_column(String(16), nullable=False, default="und")
    body_length: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    depth: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="visible")
    like_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class Comment(Base, TimestampMixin):
    __tablename__ = "comments"
    __table_args__ = (
        CheckConstraint("depth between 1 and 3", name="ck_comments_depth"),
        CheckConstraint(
            "status in ('visible','hidden','deleted')", name="ck_comments_status"
        ),
        CheckConstraint("like_count >= 0", name="ck_comments_like_count_non_negative"),
        CheckConstraint(
            "upvote_count >= 0", name="ck_comments_upvote_count_non_negative"
        ),
        CheckConstraint(
            "downvote_count >= 0", name="ck_comments_downvote_count_non_negative"
        ),
        Index("ix_comments_thread_created", "thread_id", "created_at"),
        Index("ix_comments_parent_created", "parent_comment_id", "created_at"),
        Index("ix_comments_root_created", "root_comment_id", "created_at"),
        Index("ix_comments_author_created", "author_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    thread_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("threads.id", ondelete="CASCADE"), nullable=False
    )
    parent_comment_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("comments.id", ondelete="CASCADE"),
    )
    root_comment_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("comments.id", ondelete="CASCADE"),
    )
    author_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    reply_to_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT")
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    author_role_label: Mapped[str | None] = mapped_column(String(128))
    source_lang: Mapped[str] = mapped_column(String(16), nullable=False, default="und")
    body_length: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    answer_summary: Mapped[str | None] = mapped_column(Text)
    depth: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="visible")
    like_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    upvote_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    downvote_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    via_bot: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )


class Like(Base):
    __tablename__ = "likes"
    __table_args__ = (
        CheckConstraint(
            "target_type in ('thread','comment')", name="ck_likes_target_type"
        ),
        Index("ix_likes_target_created", "target_type", "target_id", "created_at"),
        Index("ix_likes_user_created", "user_id", "created_at"),
        Index(
            "uq_like_user_target", "user_id", "target_type", "target_id", unique=True
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    target_type: Mapped[str] = mapped_column(String(16), nullable=False)
    target_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AnswerVote(Base):
    __tablename__ = "answer_votes"
    __table_args__ = (
        CheckConstraint("vote in (1,-1)", name="ck_answer_votes_vote"),
        Index("ix_answer_votes_comment_created", "comment_id", "created_at"),
        Index("ix_answer_votes_user_created", "user_id", "created_at"),
        Index("uq_answer_vote_user_comment", "user_id", "comment_id", unique=True),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    comment_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("comments.id", ondelete="CASCADE"), nullable=False
    )
    vote: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ContentTranslation(Base, TimestampMixin):
    __tablename__ = "content_translations"
    __table_args__ = (
        CheckConstraint(
            "target_type in ('thread','comment')",
            name="ck_content_translations_target_type",
        ),
        CheckConstraint(
            "field_name in ('title','abstract','body','summary','answer_summary')",
            name="ck_content_translations_field_name",
        ),
        Index(
            "uq_content_translation_target_field_lang",
            "target_type",
            "target_id",
            "field_name",
            "lang",
            unique=True,
        ),
        Index(
            "ix_content_translation_target",
            "target_type",
            "target_id",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    target_type: Mapped[str] = mapped_column(String(16), nullable=False)
    target_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    field_name: Mapped[str] = mapped_column(String(32), nullable=False)
    lang: Mapped[str] = mapped_column(String(16), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="manual")
