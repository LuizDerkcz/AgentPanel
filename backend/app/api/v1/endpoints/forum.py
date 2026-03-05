from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import math
import random
import re
from uuid import uuid4
from typing import Any, Literal

THREAD_PATH_RE = re.compile(r"^/question/(\d+)")

from fastapi import APIRouter, Depends, Header, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import String, and_, func, literal, or_, select, union_all, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.deps.auth import get_current_demo_user
from app.api.v1.shared import AuthorSummaryOut, build_author_map
from app.core.config import get_settings
from app.core.error_codes import (
    ANSWER_VOTE_ONLY_FOR_ANSWER,
    CATEGORY_NAME_EXISTS,
    CATEGORY_NAME_OR_SLUG_EXISTS,
    CATEGORY_NOT_FOUND,
    CATEGORY_NOT_FOUND_OR_INACTIVE,
    CATEGORY_SLUG_EXISTS,
    COMMENT_DELETE_FORBIDDEN,
    COMMENT_DEPTH_EXCEEDED,
    COMMENT_BODY_TOO_LONG,
    COMMENT_MODIFY_FORBIDDEN,
    COMMENT_LIKE_NOT_ALLOWED_FOR_ANSWER,
    COMMENT_NOT_FOUND,
    CONTENT_CONTAINS_SENSITIVE_WORDS,
    INVALID_LIKE_TARGET_TYPE,
    INVALID_RECOMMENDATION_CURSOR,
    HUMAN_DAILY_THREAD_LIMIT_REACHED,
    LIKE_ALREADY_EXISTS,
    LIKE_NOT_FOUND,
    PARENT_COMMENT_NOT_FOUND,
    THREAD_BODY_TOO_SHORT,
    THREAD_DELETE_FORBIDDEN,
    THREAD_MODIFY_FORBIDDEN,
    THREAD_NOT_FOUND,
    THREAD_TITLE_TOO_SHORT,
)
from app.services.content_filter import find_hits, find_hits_llm, find_hits_names, has_non_chinese, extract_person_names
from app.core.errors import api_error
from app.core.security import decode_access_token
from app.db.session import get_db
from app.models.agent import AgentProfile
from app.models.analytics import PageViewEvent
from app.models.forum import (
    AnswerVote,
    Category,
    Column,
    ColumnComment,
    Comment,
    Like,
    Thread,
)
from app.models.system_setting import SystemSetting
from app.models.user import User
from app.services.forum_metrics import (
    refresh_answer_vote_counts,
    refresh_like_count,
    refresh_thread_reply_count,
)
from app.services.message_outbox import (
    build_dedupe_key,
    enqueue_event,
    extract_mentions,
    process_pending_events,
)


router = APIRouter(prefix="/forum", tags=["forum"])
settings = get_settings()

ANSWER_MAX_LENGTH = 5000
REPLY_MAX_LENGTH = 500
ENGLISH_WORD_RE = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?")
CHINESE_CHAR_RE = re.compile(r"[\u4e00-\u9fff]")


def _normalize_model_label(model_name: str | None) -> str:
    text = (model_name or "").strip()
    if not text:
        return ""
    if "/" not in text:
        return text
    return text.split("/", 1)[1].strip() or text


def _resolve_comment_author_role_label(db: Session, user: User) -> str:
    if user.user_type == "human":
        return "human"
    if user.user_type == "agent":
        profile = db.scalar(select(AgentProfile).where(AgentProfile.user_id == user.id))
        if profile and profile.switchable is False:
            model_label = _normalize_model_label(profile.default_model)
            if model_label:
                return model_label
    return "agent"


def _count_english_words(text: str) -> int:
    return len(ENGLISH_WORD_RE.findall(text))


def _count_chinese_chars(text: str) -> int:
    return len(CHINESE_CHAR_RE.findall(text))


def _meets_multilingual_minimum(
    text: str,
    *,
    min_english_words: int,
    min_chinese_chars: int,
) -> bool:
    return (
        _count_english_words(text) >= min_english_words
        or _count_chinese_chars(text) >= min_chinese_chars
    )


def _count_zh_or_en_units(text: str) -> int:
    return _count_chinese_chars(text) + _count_english_words(text)


def _extract_thread_id_from_path(path: str | None) -> int | None:
    text = str(path or "").strip()
    match = THREAD_PATH_RE.match(text)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _resolve_optional_user_from_headers(
    db: Session,
    *,
    authorization: str | None,
    x_demo_user: str | None,
) -> User | None:
    if authorization:
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() == "bearer" and token:
            try:
                payload = decode_access_token(
                    token=token,
                    secret_key=settings.auth_secret_key,
                )
                user_id = payload.get("uid")
                if isinstance(user_id, int):
                    user = db.get(User, user_id)
                    if user and user.status == "active":
                        return user
            except Exception:
                pass

    if x_demo_user:
        user = db.scalar(
            select(User).where(User.username == x_demo_user, User.status == "active")
        )
        if user:
            return user

    return None


class CategoryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    slug: str = Field(min_length=1, max_length=64)
    description: str | None = None
    sort_order: int = 100

    @field_validator("name", "slug", mode="before")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        if not isinstance(value, str):
            raise ValueError("must be a string")
        value = value.strip()
        if not value:
            raise ValueError("must not be empty")
        return value


class CategoryUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    slug: str | None = Field(default=None, min_length=1, max_length=64)
    description: str | None = None
    sort_order: int | None = None
    is_active: bool | None = None

    @field_validator("name", "slug", mode="before")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return value
        value = value.strip()
        if not value:
            raise ValueError("must not be empty")
        return value


class CategoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    slug: str
    description: str | None = None
    sort_order: int
    is_active: bool
    created_at: datetime
    updated_at: datetime


class ThreadCreate(BaseModel):
    category_id: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=200)
    abstract: str | None = Field(default=None, max_length=500)
    body: str = Field(min_length=1)
    source_lang: Literal["zh", "en", "und"] | None = "und"
    status: Literal["draft", "published", "locked", "deleted"] = "published"
    is_pinned: bool = False

    @field_validator("title", "body", mode="before")
    @classmethod
    def normalize_thread_required_text(cls, value: str) -> str:
        if not isinstance(value, str):
            raise ValueError("must be a string")
        value = value.strip()
        if not value:
            raise ValueError("must not be empty")
        return value

    @field_validator("abstract", mode="before")
    @classmethod
    def normalize_thread_optional_abstract(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return value.strip()


class ThreadUpdate(BaseModel):
    category_id: int | None = Field(default=None, ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=200)
    abstract: str | None = Field(default=None, max_length=500)
    body: str | None = Field(default=None, min_length=1)
    status: Literal["draft", "published", "locked", "deleted"] | None = None
    is_pinned: bool | None = None

    @field_validator("title", "body", mode="before")
    @classmethod
    def normalize_thread_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return value
        value = value.strip()
        if not value:
            raise ValueError("must not be empty")
        return value

    @field_validator("abstract", mode="before")
    @classmethod
    def normalize_thread_optional_abstract_update(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return value.strip()


class ThreadOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    category_id: int
    author_id: int
    title: str
    abstract: str | None = None
    body: str
    status: str
    is_pinned: bool
    pinned_at: datetime | None = None
    reply_count: int
    like_count: int
    view_count: int
    last_activity_at: datetime
    created_at: datetime
    updated_at: datetime
    author: AuthorSummaryOut | None = None
    summary: str | None = None


class CommentCreate(BaseModel):
    body: str = Field(min_length=1)

    @field_validator("body", mode="before")
    @classmethod
    def normalize_comment_body(cls, value: str) -> str:
        if not isinstance(value, str):
            raise ValueError("must be a string")
        value = value.strip()
        if not value:
            raise ValueError("must not be empty")
        return value


class CommentUpdate(BaseModel):
    body: str = Field(min_length=1)

    @field_validator("body", mode="before")
    @classmethod
    def normalize_comment_update_body(cls, value: str) -> str:
        if not isinstance(value, str):
            raise ValueError("must be a string")
        value = value.strip()
        if not value:
            raise ValueError("must not be empty")
        return value


class CommentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    thread_id: int
    parent_comment_id: int | None = None
    root_comment_id: int | None = None
    author_id: int
    reply_to_user_id: int | None = None
    body: str
    depth: int
    status: str
    like_count: int
    upvote_count: int
    downvote_count: int
    author_role_label: str | None = None
    created_at: datetime
    updated_at: datetime
    author: AuthorSummaryOut | None = None
    reply_to_author: AuthorSummaryOut | None = None
    answer_summary: str | None = None
    source_lang: str = "und"


class LikeUpsert(BaseModel):
    target_type: Literal["thread", "comment"]
    target_id: int = Field(ge=1)


class LikeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    target_type: str
    target_id: int
    created_at: datetime


class AnswerVoteInput(BaseModel):
    vote: Literal["up", "down", "cancel"]


class AnswerVoteOut(BaseModel):
    comment_id: int
    upvote_count: int
    downvote_count: int
    my_vote: Literal["up", "down", "none"]


class MyAnswerVoteOut(BaseModel):
    comment_id: int
    vote: Literal["up", "down"]


class ThreadRecommendationPageOut(BaseModel):
    items: list[ThreadOut]
    seed: str
    next_cursor: str | None = None
    has_more: bool


class FeedThreadItem(BaseModel):
    thread: ThreadOut
    selected_answer: CommentOut | None = None


class FeedPageOut(BaseModel):
    pinned: list[FeedThreadItem]
    items: list[FeedThreadItem]
    has_more: bool


class ThreadCountOut(BaseModel):
    count: int


class HomeStatsOut(BaseModel):
    human_user_count: int
    ai_agent_count: int
    daily_active_users: int
    daily_visit_volume: int


class RecentCommentSnippet(BaseModel):
    display_name: str
    role_label: str
    body: str


def _resolve_snippet_role_label(user_row, agent_profile_row) -> str:
    if user_row is None:
        return "agent"
    if user_row.user_type == "human":
        return "human"
    if agent_profile_row and agent_profile_row.default_model:
        return _normalize_model_label(agent_profile_row.default_model)
    return "agent"


def _strip_markdown(text: str) -> str:
    import re

    text = re.sub(r"\$\$.*?\$\$", "", text, flags=re.DOTALL)
    text = re.sub(r"\$[^$\n]+?\$", "", text)
    text = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"!\[.*?\]\(.*?\)", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"\*{1,2}([^*\n]+)\*{1,2}", r"\1", text)
    text = re.sub(r"_{1,2}([^_\n]+)_{1,2}", r"\1", text)
    text = re.sub(r"^>\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"^[\-\*\+]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\d+\.\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^[-*]{3,}$", "", text, flags=re.MULTILINE)
    return text.strip()


class RealtimeHotTopicItemOut(BaseModel):
    thread_id: int
    title: str
    created_at: datetime
    answer_count: int
    reply_count: int
    like_count: int
    view_count: int
    window_answer_delta: int
    window_reply_delta: int
    window_like_delta: int
    window_view_delta: int
    summary: str | None = None
    debate_summary: str | None = None
    debate_score: float
    spike_score: float
    realtime_score: float
    recent_comments: list[RecentCommentSnippet] = []


class RealtimeHotTopicsOut(BaseModel):
    window_hours: int
    items: list[RealtimeHotTopicItemOut]


class PageViewIn(BaseModel):
    path: str = Field(min_length=1, max_length=512)
    visitor_id: str = Field(min_length=8, max_length=64)
    session_id: str | None = Field(default=None, max_length=64)

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("path cannot be empty")
        if not text.startswith("/"):
            text = f"/{text}"
        return text[:512]

    @field_validator("visitor_id", "session_id")
    @classmethod
    def normalize_identifier(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip()
        return text or None


class UserActivityItem(BaseModel):
    user_id: int
    post_count: int
    comment_count: int


class UserActivityOut(BaseModel):
    items: list[UserActivityItem]


def serialize_thread(
    thread: Thread, author_map: dict[int, AuthorSummaryOut]
) -> ThreadOut:
    return ThreadOut.model_validate(
        {
            "id": thread.id,
            "category_id": thread.category_id,
            "author_id": thread.author_id,
            "title": thread.title,
            "abstract": thread.abstract,
            "body": thread.body,
            "status": thread.status,
            "is_pinned": thread.is_pinned,
            "pinned_at": thread.pinned_at,
            "reply_count": thread.reply_count,
            "like_count": thread.like_count,
            "view_count": thread.view_count,
            "last_activity_at": thread.last_activity_at,
            "created_at": thread.created_at,
            "updated_at": thread.updated_at,
            "author": author_map.get(thread.author_id),
            "summary": thread.summary,
        }
    )


def serialize_comment(
    comment: Comment, author_map: dict[int, AuthorSummaryOut]
) -> CommentOut:
    return CommentOut.model_validate(
        {
            "id": comment.id,
            "thread_id": comment.thread_id,
            "parent_comment_id": comment.parent_comment_id,
            "root_comment_id": comment.root_comment_id,
            "author_id": comment.author_id,
            "reply_to_user_id": comment.reply_to_user_id,
            "body": comment.body,
            "depth": comment.depth,
            "status": comment.status,
            "like_count": comment.like_count,
            "upvote_count": comment.upvote_count,
            "downvote_count": comment.downvote_count,
            "author_role_label": comment.author_role_label,
            "created_at": comment.created_at,
            "updated_at": comment.updated_at,
            "author": author_map.get(comment.author_id),
            "reply_to_author": author_map.get(comment.reply_to_user_id),
            "answer_summary": comment.answer_summary,
        }
    )


def parse_recommendation_cursor(cursor: str) -> tuple[str, int]:
    parts = cursor.split(":", 1)
    if len(parts) != 2:
        raise api_error(
            status_code=status.HTTP_400_BAD_REQUEST,
            code=INVALID_RECOMMENDATION_CURSOR,
            message="Invalid recommendation cursor format.",
        )

    hash_key, raw_id = parts
    if len(hash_key) != 32 or any(ch not in "0123456789abcdef" for ch in hash_key):
        raise api_error(
            status_code=status.HTTP_400_BAD_REQUEST,
            code=INVALID_RECOMMENDATION_CURSOR,
            message="Invalid recommendation cursor hash.",
        )

    try:
        thread_id = int(raw_id)
    except ValueError as exc:
        raise api_error(
            status_code=status.HTTP_400_BAD_REQUEST,
            code=INVALID_RECOMMENDATION_CURSOR,
            message="Invalid recommendation cursor thread id.",
        ) from exc

    if thread_id <= 0:
        raise api_error(
            status_code=status.HTTP_400_BAD_REQUEST,
            code=INVALID_RECOMMENDATION_CURSOR,
            message="Invalid recommendation cursor thread id.",
        )

    return hash_key, thread_id


def build_recommendation_cursor(hash_key: str, thread_id: int) -> str:
    return f"{hash_key}:{thread_id}"


def _compose_realtime_summary(
    *,
    thread_summary: str | None,
    debate_summary: str | None,
    debate_score: float | None,
) -> str | None:
    thread_text = (thread_summary or "").strip()
    debate_text = (debate_summary or "").strip()

    if debate_text and debate_score is not None and float(debate_score) >= 35:
        return debate_text
    if thread_text:
        return thread_text
    if debate_text:
        return debate_text
    return None


def validate_like_target(db: Session, target_type: str, target_id: int) -> None:
    if target_type == "thread":
        thread = db.get(Thread, target_id)
        if not thread or thread.status == "deleted":
            raise api_error(
                status_code=status.HTTP_404_NOT_FOUND,
                code=THREAD_NOT_FOUND,
                message="Thread not found.",
            )
        return

    if target_type == "comment":
        comment = db.get(Comment, target_id)
        if not comment or comment.status == "deleted":
            raise api_error(
                status_code=status.HTTP_404_NOT_FOUND,
                code=COMMENT_NOT_FOUND,
                message="Comment not found.",
            )
        return

    raise api_error(
        status_code=status.HTTP_400_BAD_REQUEST,
        code=INVALID_LIKE_TARGET_TYPE,
        message="target_type must be 'thread' or 'comment'.",
    )


def enqueue_mention_events(
    db: Session,
    *,
    body: str,
    actor_user: User,
    thread_id: int,
    comment_id: int,
    parent_comment_id: int | None,
    depth: int,
) -> None:
    mention_usernames = extract_mentions(body)
    if not mention_usernames:
        return

    mention_users = list(
        db.scalars(
            select(User).where(
                User.username.in_(mention_usernames),
                User.status == "active",
            )
        ).all()
    )
    for target_user in mention_users:
        if target_user.id == actor_user.id:
            continue

        action_hint = (
            "consider_reply" if target_user.user_type == "agent" else "notify_only"
        )
        dedupe_key = build_dedupe_key(
            event_type="mention.created",
            target_user_id=target_user.id,
            target_id=comment_id,
        )
        enqueue_event(
            db,
            event_type="mention.created",
            actor_user_id=actor_user.id,
            target_user_id=target_user.id,
            target_user_type=target_user.user_type,
            thread_id=thread_id,
            comment_id=comment_id,
            parent_comment_id=parent_comment_id,
            depth=depth,
            action_hint=action_hint,
            dedupe_key=dedupe_key,
            payload={
                "mentioned_username": target_user.username,
                "content_preview": body[:500],
                "language": "zh",
            },
        )


@router.get("/ping")
def ping_forum() -> dict[str, str]:
    return {"app": "forum", "status": "ok"}


@router.get("/categories", response_model=list[CategoryOut])
def list_categories(
    include_inactive: bool = Query(default=False),
    db: Session = Depends(get_db),
) -> list[Category]:
    query = select(Category).order_by(Category.sort_order.asc(), Category.id.asc())
    if not include_inactive:
        query = query.where(Category.is_active.is_(True))
    return list(db.scalars(query).all())


@router.post(
    "/categories", response_model=CategoryOut, status_code=status.HTTP_201_CREATED
)
def create_category(
    payload: CategoryCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_demo_user),
) -> Category:
    _ = current_user

    exists = db.scalar(
        select(Category).where(
            (Category.name == payload.name) | (Category.slug == payload.slug)
        )
    )
    if exists:
        raise api_error(
            status_code=status.HTTP_409_CONFLICT,
            code=CATEGORY_NAME_OR_SLUG_EXISTS,
            message="Category name or slug already exists.",
        )

    category = Category(
        name=payload.name,
        slug=payload.slug,
        description=payload.description,
        sort_order=payload.sort_order,
        is_active=True,
    )
    db.add(category)
    db.commit()
    db.refresh(category)
    return category


@router.patch("/categories/{category_id}", response_model=CategoryOut)
def update_category(
    category_id: int,
    payload: CategoryUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_demo_user),
) -> Category:
    _ = current_user

    category = db.get(Category, category_id)
    if not category:
        raise api_error(
            status_code=status.HTTP_404_NOT_FOUND,
            code=CATEGORY_NOT_FOUND,
            message="Category not found.",
        )

    if payload.name and payload.name != category.name:
        name_taken = db.scalar(
            select(Category).where(
                Category.name == payload.name, Category.id != category.id
            )
        )
        if name_taken:
            raise api_error(
                status_code=status.HTTP_409_CONFLICT,
                code=CATEGORY_NAME_EXISTS,
                message="Category name already exists.",
            )
        category.name = payload.name

    if payload.slug and payload.slug != category.slug:
        slug_taken = db.scalar(
            select(Category).where(
                Category.slug == payload.slug, Category.id != category.id
            )
        )
        if slug_taken:
            raise api_error(
                status_code=status.HTTP_409_CONFLICT,
                code=CATEGORY_SLUG_EXISTS,
                message="Category slug already exists.",
            )
        category.slug = payload.slug

    if payload.description is not None:
        category.description = payload.description
    if payload.sort_order is not None:
        category.sort_order = payload.sort_order
    if payload.is_active is not None:
        category.is_active = payload.is_active

    db.commit()
    db.refresh(category)
    return category


@router.delete(
    "/categories/{category_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def delete_category(
    category_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_demo_user),
) -> Response:
    _ = current_user

    category = db.get(Category, category_id)
    if not category:
        raise api_error(
            status_code=status.HTTP_404_NOT_FOUND,
            code=CATEGORY_NOT_FOUND,
            message="Category not found.",
        )

    category.is_active = False
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/threads", response_model=list[ThreadOut])
def list_threads(
    category_id: int | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
    sort_by: str = Query(default="time"),
    source_lang: str | None = Query(default=None),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> list[ThreadOut]:
    query = select(Thread)

    if category_id is not None:
        query = query.where(Thread.category_id == category_id)

    if status_filter is None:
        query = query.where(Thread.status != "deleted")
    else:
        query = query.where(Thread.status == status_filter)

    if source_lang:
        query = query.where(Thread.source_lang == source_lang)

    if sort_by == "hots":
        hot_score = Thread.like_count * 3 + Thread.reply_count * 2 + Thread.view_count
        query = query.order_by(Thread.is_pinned.desc(), hot_score.desc())
    elif sort_by == "length":
        content_length = (
            func.length(Thread.title)
            + func.coalesce(func.length(Thread.abstract), 0)
            + func.length(Thread.body)
        )
        query = query.order_by(Thread.is_pinned.desc(), content_length.desc())
    else:
        query = query.order_by(Thread.is_pinned.desc(), Thread.last_activity_at.desc())

    query = query.offset(offset).limit(limit)
    threads = list(db.scalars(query).all())
    author_map = build_author_map(db, {thread.author_id for thread in threads})
    return [serialize_thread(thread, author_map) for thread in threads]


@router.get("/threads/realtime-hots", response_model=RealtimeHotTopicsOut)
def list_realtime_hot_threads(
    window_hours: int = Query(default=1, ge=1, le=24),
    limit: int = Query(default=10, ge=1, le=20),
    source_lang: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> RealtimeHotTopicsOut:
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(hours=window_hours)

    answer_count_rows = db.execute(
        select(Comment.thread_id, func.count(Comment.id).label("cnt"))
        .where(
            Comment.depth == 1,
            Comment.status != "deleted",
        )
        .group_by(Comment.thread_id)
    ).all()
    answer_count_map = {int(row.thread_id): int(row.cnt) for row in answer_count_rows}

    candidate_ids = [
        thread_id
        for thread_id, answer_count in answer_count_map.items()
        if answer_count >= 3
    ]
    if not candidate_ids:
        return RealtimeHotTopicsOut(window_hours=window_hours, items=[])

    thread_filter = [Thread.id.in_(candidate_ids), Thread.status != "deleted"]
    if source_lang:
        thread_filter.append(Thread.source_lang == source_lang)
    threads = list(
        db.scalars(select(Thread).where(*thread_filter)).all()
    )
    if not threads:
        return RealtimeHotTopicsOut(window_hours=window_hours, items=[])

    thread_map = {int(thread.id): thread for thread in threads}
    active_ids = list(thread_map.keys())

    recent_answer_rows = db.execute(
        select(Comment.thread_id, func.count(Comment.id).label("cnt"))
        .where(
            Comment.thread_id.in_(active_ids),
            Comment.depth == 1,
            Comment.status != "deleted",
            Comment.created_at >= window_start,
            Comment.created_at < now,
        )
        .group_by(Comment.thread_id)
    ).all()
    recent_answer_map = {int(row.thread_id): int(row.cnt) for row in recent_answer_rows}

    recent_reply_rows = db.execute(
        select(Comment.thread_id, func.count(Comment.id).label("cnt"))
        .where(
            Comment.thread_id.in_(active_ids),
            Comment.depth > 1,
            Comment.status != "deleted",
            Comment.created_at >= window_start,
            Comment.created_at < now,
        )
        .group_by(Comment.thread_id)
    ).all()
    recent_reply_map = {int(row.thread_id): int(row.cnt) for row in recent_reply_rows}

    recent_thread_like_rows = db.execute(
        select(Like.target_id, func.count(Like.id).label("cnt"))
        .where(
            Like.target_type == "thread",
            Like.target_id.in_(active_ids),
            Like.created_at >= window_start,
            Like.created_at < now,
        )
        .group_by(Like.target_id)
    ).all()
    recent_thread_like_map = {
        int(row.target_id): int(row.cnt) for row in recent_thread_like_rows
    }

    recent_comment_like_rows = db.execute(
        select(Comment.thread_id, func.count(Like.id).label("cnt"))
        .join(
            Comment, and_(Like.target_type == "comment", Like.target_id == Comment.id)
        )
        .where(
            Comment.thread_id.in_(active_ids),
            Like.created_at >= window_start,
            Like.created_at < now,
        )
        .group_by(Comment.thread_id)
    ).all()
    recent_comment_like_map = {
        int(row.thread_id): int(row.cnt) for row in recent_comment_like_rows
    }

    vote_rows = db.execute(
        select(
            Comment.thread_id,
            func.coalesce(func.sum(Comment.upvote_count), 0).label("upvotes"),
            func.coalesce(func.sum(Comment.downvote_count), 0).label("downvotes"),
        )
        .where(
            Comment.thread_id.in_(active_ids),
            Comment.depth == 1,
            Comment.status != "deleted",
        )
        .group_by(Comment.thread_id)
    ).all()
    vote_map = {
        int(row.thread_id): {
            "up": int(row.upvotes or 0),
            "down": int(row.downvotes or 0),
        }
        for row in vote_rows
    }

    recent_view_rows = db.execute(
        select(PageViewEvent.path, func.count(PageViewEvent.id).label("cnt"))
        .where(
            PageViewEvent.created_at >= window_start,
            PageViewEvent.created_at < now,
            PageViewEvent.path.like("/question/%"),
        )
        .group_by(PageViewEvent.path)
    ).all()
    recent_view_map: dict[int, int] = {}
    for row in recent_view_rows:
        thread_id = _extract_thread_id_from_path(row.path)
        if not thread_id or thread_id not in thread_map:
            continue
        recent_view_map[thread_id] = recent_view_map.get(thread_id, 0) + int(row.cnt)

    base_rows: list[dict] = []
    max_spike_raw = 0.0

    for thread in thread_map.values():
        thread_id = int(thread.id)
        answer_count = int(answer_count_map.get(thread_id, 0))
        if answer_count < 3:
            continue

        window_answer_delta = int(recent_answer_map.get(thread_id, 0))
        window_reply_delta = int(recent_reply_map.get(thread_id, 0))
        window_like_delta = int(recent_thread_like_map.get(thread_id, 0)) + int(
            recent_comment_like_map.get(thread_id, 0)
        )
        window_view_delta = int(recent_view_map.get(thread_id, 0))

        vote_data = vote_map.get(thread_id, {"up": 0, "down": 0})
        upvotes = int(vote_data["up"])
        downvotes = int(vote_data["down"])
        vote_total = upvotes + downvotes
        vote_balance = (min(upvotes, downvotes) / vote_total) if vote_total > 0 else 0.0

        heuristic_debate_score = min(
            100.0,
            vote_balance * 70.0
            + min(30.0, answer_count * 2.0 + window_reply_delta * 1.5),
        )
        debate_score = (
            float(thread.debate_score)
            if thread.debate_score is not None
            else float(heuristic_debate_score)
        )

        spike_raw = (
            window_answer_delta * 3.0
            + window_reply_delta * 2.0
            + window_like_delta * 2.5
            + window_view_delta * 1.0
        )
        max_spike_raw = max(max_spike_raw, spike_raw)

        base_rows.append(
            {
                "thread": thread,
                "answer_count": answer_count,
                "window_answer_delta": window_answer_delta,
                "window_reply_delta": window_reply_delta,
                "window_like_delta": window_like_delta,
                "window_view_delta": window_view_delta,
                "debate_score": round(debate_score, 2),
                "debate_summary": thread.debate_summary,
                "spike_raw": spike_raw,
            }
        )

    if not base_rows:
        return RealtimeHotTopicsOut(window_hours=window_hours, items=[])

    realtime_items: list[RealtimeHotTopicItemOut] = []
    for row in base_rows:
        thread = row["thread"]
        spike_score = (
            (row["spike_raw"] / max_spike_raw) * 100.0 if max_spike_raw > 0 else 0.0
        )
        has_ai_composite = (
            row["debate_summary"] is not None and row["debate_score"] is not None
        )
        realtime_score = (
            float(row["debate_score"])
            if has_ai_composite
            else row["debate_score"] * 0.55 + spike_score * 0.45
        )
        realtime_items.append(
            RealtimeHotTopicItemOut(
                thread_id=int(thread.id),
                title=thread.title,
                created_at=thread.created_at,
                answer_count=int(row["answer_count"]),
                reply_count=max(0, int(thread.reply_count) - int(row["answer_count"])),
                like_count=int(thread.like_count),
                view_count=int(thread.view_count),
                window_answer_delta=int(row["window_answer_delta"]),
                window_reply_delta=int(row["window_reply_delta"]),
                window_like_delta=int(row["window_like_delta"]),
                window_view_delta=int(row["window_view_delta"]),
                summary=_compose_realtime_summary(
                    thread_summary=thread.summary,
                    debate_summary=row["debate_summary"],
                    debate_score=row["debate_score"],
                ),
                debate_summary=row["debate_summary"],
                debate_score=round(float(row["debate_score"]), 2),
                spike_score=round(float(spike_score), 2),
                realtime_score=round(float(realtime_score), 2),
            )
        )

    realtime_items.sort(
        key=lambda item: (
            item.realtime_score,
            item.view_count,
            item.window_answer_delta,
            item.window_reply_delta,
            item.window_like_delta,
            item.thread_id,
        ),
        reverse=True,
    )

    # 附带每帖最近 5 条评论
    from collections import defaultdict

    top_items = realtime_items[:limit]
    thread_ids_out = [item.thread_id for item in top_items]
    if thread_ids_out:
        recent_rows = db.execute(
            select(
                Comment.thread_id, Comment.author_id, Comment.body, Comment.created_at
            )
            .where(
                Comment.thread_id.in_(thread_ids_out),
                Comment.status == "visible",
            )
            .order_by(Comment.thread_id, Comment.created_at.desc())
        ).all()

        recent_by_thread: dict[int, list] = defaultdict(list)
        for row in recent_rows:
            if len(recent_by_thread[row.thread_id]) < 5:
                recent_by_thread[row.thread_id].append(row)

        all_author_ids = {
            row.author_id for rows in recent_by_thread.values() for row in rows
        }
        author_map: dict[int, Any] = {}
        agent_profile_map: dict[int, Any] = {}
        if all_author_ids:
            author_rows = db.execute(
                select(User.id, User.display_name, User.user_type).where(
                    User.id.in_(all_author_ids)
                )
            ).all()
            author_map = {r.id: r for r in author_rows}
            agent_profile_rows = db.execute(
                select(
                    AgentProfile.user_id,
                    AgentProfile.switchable,
                    AgentProfile.default_model,
                ).where(AgentProfile.user_id.in_(all_author_ids))
            ).all()
            agent_profile_map = {r.user_id: r for r in agent_profile_rows}

        for item in top_items:
            snippets = []
            for row in recent_by_thread.get(item.thread_id, []):
                body = str(row.body or "").replace("\n", " ").strip()
                body = _strip_markdown(body)
                body = body[:80] + ("…" if len(body) > 80 else "")
                snippets.append(
                    RecentCommentSnippet(
                        display_name=(
                            author_map[row.author_id].display_name
                            if row.author_id in author_map
                            else ""
                        ),
                        role_label=_resolve_snippet_role_label(
                            author_map.get(row.author_id),
                            agent_profile_map.get(row.author_id),
                        ),
                        body=body,
                    )
                )
            item.recent_comments = snippets

    return RealtimeHotTopicsOut(
        window_hours=window_hours,
        items=top_items,
    )


@router.get("/threads/count", response_model=ThreadCountOut)
def get_threads_count(
    category_id: int | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
    source_lang: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> ThreadCountOut:
    query = select(func.count()).select_from(Thread)

    if category_id is not None:
        query = query.where(Thread.category_id == category_id)

    if status_filter is None:
        query = query.where(Thread.status != "deleted")
    else:
        query = query.where(Thread.status == status_filter)

    if source_lang:
        query = query.where(Thread.source_lang == source_lang)

    total = int(db.scalar(query) or 0)
    return ThreadCountOut(count=total)


_home_stats_cache: dict = {"data": None, "expires": 0.0}
_HOME_STATS_TTL = 300  # 5 分钟


@router.get("/home-stats", response_model=HomeStatsOut)
def get_home_stats(db: Session = Depends(get_db)) -> HomeStatsOut:
    import time
    now_ts = time.monotonic()
    if _home_stats_cache["data"] and now_ts < _home_stats_cache["expires"]:
        return _home_stats_cache["data"]

    now = datetime.now(timezone.utc)
    local_tz = timezone(timedelta(hours=8))
    window_end_local = now.astimezone(local_tz).replace(
        minute=0,
        second=0,
        microsecond=0,
    )
    window_start_local = window_end_local - timedelta(hours=24)
    window_start = window_start_local.astimezone(timezone.utc)
    window_end = window_end_local.astimezone(timezone.utc)

    human_user_count = (
        db.scalar(
            select(func.count(User.id)).where(
                User.email.is_not(None),
                func.length(func.trim(User.email)) > 0,
            )
        )
        or 0
    )
    ai_agent_count = db.scalar(select(func.count(AgentProfile.id))) or 0

    action_identity_events = union_all(
        select(
            func.concat(literal("u:"), Thread.author_id.cast(String)).label("identity")
        )
        .join(User, User.id == Thread.author_id)
        .where(
            Thread.created_at >= window_start,
            Thread.created_at < window_end,
            Thread.status != "deleted",
            User.user_type == "human",
        ),
        select(
            func.concat(literal("u:"), Comment.author_id.cast(String)).label("identity")
        )
        .join(User, User.id == Comment.author_id)
        .where(
            Comment.created_at >= window_start,
            Comment.created_at < window_end,
            Comment.status != "deleted",
            or_(
                Comment.author_role_label == "human",
                and_(Comment.author_role_label.is_(None), User.user_type == "human"),
            ),
        ),
        select(
            func.concat(literal("u:"), Column.author_id.cast(String)).label("identity")
        )
        .join(User, User.id == Column.author_id)
        .where(
            Column.created_at >= window_start,
            Column.created_at < window_end,
            Column.status != "deleted",
            User.user_type == "human",
        ),
        select(
            func.concat(literal("u:"), ColumnComment.author_id.cast(String)).label(
                "identity"
            )
        )
        .join(User, User.id == ColumnComment.author_id)
        .where(
            ColumnComment.created_at >= window_start,
            ColumnComment.created_at < window_end,
            ColumnComment.status != "deleted",
            User.user_type == "human",
        ),
        select(func.concat(literal("u:"), Like.user_id.cast(String)).label("identity"))
        .join(User, User.id == Like.user_id)
        .where(
            Like.created_at >= window_start,
            Like.created_at < window_end,
            User.user_type == "human",
        ),
        select(
            func.concat(literal("u:"), AnswerVote.user_id.cast(String)).label(
                "identity"
            )
        )
        .join(User, User.id == AnswerVote.user_id)
        .where(
            AnswerVote.created_at >= window_start,
            AnswerVote.created_at < window_end,
            User.user_type == "human",
        ),
    ).subquery()

    try:
        dau_identity_events = union_all(
            select(action_identity_events.c.identity),
            select(
                func.concat(literal("u:"), PageViewEvent.user_id.cast(String)).label(
                    "identity"
                )
            )
            .join(User, User.id == PageViewEvent.user_id)
            .where(
                PageViewEvent.user_id.is_not(None),
                PageViewEvent.created_at >= window_start,
                PageViewEvent.created_at < window_end,
                User.user_type == "human",
            ),
            select(
                func.concat(literal("v:"), PageViewEvent.visitor_id).label("identity")
            ).where(
                PageViewEvent.user_id.is_(None),
                PageViewEvent.created_at >= window_start,
                PageViewEvent.created_at < window_end,
            ),
        ).subquery()

        daily_active_users = (
            db.scalar(select(func.count(func.distinct(dau_identity_events.c.identity))))
            or 0
        )
        total_page_view_volume = db.scalar(select(func.count(PageViewEvent.id))) or 0
        visit_base_offset_raw = db.scalar(
            select(SystemSetting.value).where(SystemSetting.key == "visit_base_offset")
        )
    except SQLAlchemyError:
        db.rollback()
        daily_active_users = 0
        total_page_view_volume = 0
        visit_base_offset_raw = "0"

    try:
        visit_base_offset = int(str(visit_base_offset_raw or "0").strip())
    except ValueError:
        visit_base_offset = 0

    daily_active_users = int(daily_active_users)
    daily_visit_volume = int(total_page_view_volume + max(0, visit_base_offset))

    result = HomeStatsOut(
        human_user_count=int(human_user_count),
        ai_agent_count=int(ai_agent_count),
        daily_active_users=int(daily_active_users),
        daily_visit_volume=int(daily_visit_volume),
    )
    _home_stats_cache["data"] = result
    _home_stats_cache["expires"] = time.monotonic() + _HOME_STATS_TTL
    return result


@router.post(
    "/page-views",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def create_page_view(
    payload: PageViewIn,
    db: Session = Depends(get_db),
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_demo_user: str | None = Header(default=None, alias="X-Demo-User"),
) -> Response:
    viewer = _resolve_optional_user_from_headers(
        db,
        authorization=authorization,
        x_demo_user=x_demo_user,
    )
    try:
        db.add(
            PageViewEvent(
                user_id=viewer.id if viewer else None,
                visitor_id=payload.visitor_id,
                session_id=payload.session_id,
                path=payload.path,
            )
        )
        db.commit()
    except SQLAlchemyError:
        db.rollback()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/user-activity", response_model=UserActivityOut)
def get_user_activity(db: Session = Depends(get_db)) -> UserActivityOut:
    post_counts = db.execute(
        select(Thread.author_id, func.count(Thread.id).label("post_count"))
        .where(Thread.status != "deleted")
        .group_by(Thread.author_id)
    ).all()

    comment_counts = db.execute(
        select(Comment.author_id, func.count(Comment.id).label("comment_count"))
        .where(Comment.status == "visible")
        .group_by(Comment.author_id)
    ).all()

    post_map = {row.author_id: row.post_count for row in post_counts}
    comment_map = {row.author_id: row.comment_count for row in comment_counts}

    all_user_ids = set(post_map) | set(comment_map)
    items = [
        UserActivityItem(
            user_id=uid,
            post_count=post_map.get(uid, 0),
            comment_count=comment_map.get(uid, 0),
        )
        for uid in all_user_ids
    ]
    return UserActivityOut(items=items)


@router.get("/threads/recommendations", response_model=ThreadRecommendationPageOut)
def list_recommended_threads(
    seed: str | None = Query(default=None, min_length=1, max_length=64),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=10, ge=1, le=50),
    db: Session = Depends(get_db),
) -> ThreadRecommendationPageOut:
    effective_seed = seed or uuid4().hex

    random_key_expr = func.md5(
        func.concat(literal(effective_seed), literal(":"), Thread.id.cast(String))
    ).label("random_key")

    query = select(Thread, random_key_expr).where(Thread.status != "deleted")

    if cursor:
        last_key, last_thread_id = parse_recommendation_cursor(cursor)
        query = query.where(
            or_(
                random_key_expr > last_key,
                and_(random_key_expr == last_key, Thread.id > last_thread_id),
            )
        )

    query = query.order_by(random_key_expr.asc(), Thread.id.asc()).limit(limit + 1)
    rows = list(db.execute(query).all())

    has_more = len(rows) > limit
    selected_rows = rows[:limit]

    threads = [row[0] for row in selected_rows]
    author_map = build_author_map(db, {thread.author_id for thread in threads})
    items = [serialize_thread(thread, author_map) for thread in threads]

    next_cursor = None
    if has_more and selected_rows:
        last_thread, last_key = selected_rows[-1]
        next_cursor = build_recommendation_cursor(last_key, last_thread.id)

    return ThreadRecommendationPageOut(
        items=items,
        seed=effective_seed,
        next_cursor=next_cursor,
        has_more=has_more,
    )


@router.get("/threads/feed", response_model=FeedPageOut)
def get_feed(
    limit: int = Query(default=10, ge=1, le=20),
    source_lang: str | None = Query(default=None),
    category_id: int | None = Query(default=None),
    seen_answer_ids: str | None = Query(default=None, max_length=4000),
    refresh_count: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> FeedPageOut:
    """Recommendation feed: score-based ranking + weighted-random sampling."""

    MIN_HIGH_COUNT = 5

    def _score(a: Comment) -> int:
        return a.like_count + a.upvote_count

    # ── Parse seen IDs ──
    seen: set[int] = set()
    if seen_answer_ids:
        for tok in seen_answer_ids.split(","):
            tok = tok.strip()
            if tok.isdigit():
                seen.add(int(tok))

    # ── Step 1: fetch all published threads ──
    thread_query = select(Thread).where(
        Thread.status.notin_(["deleted", "draft"])
    )
    if source_lang:
        thread_query = thread_query.where(Thread.source_lang == source_lang)
    if category_id is not None:
        thread_query = thread_query.where(Thread.category_id == category_id)

    all_threads = list(db.scalars(thread_query).all())
    thread_map = {t.id: t for t in all_threads}
    pinned_threads = [t for t in all_threads if t.is_pinned]
    candidate_thread_ids = {t.id for t in all_threads if not t.is_pinned}

    # ── Step 2: fetch all depth=1 answers ──
    all_thread_ids = list(candidate_thread_ids | {t.id for t in pinned_threads})
    all_answers: list[Comment] = []
    if all_thread_ids:
        answers_query = (
            select(Comment)
            .where(
                Comment.thread_id.in_(all_thread_ids),
                Comment.depth == 1,
                Comment.status != "deleted",
            )
            .order_by(Comment.upvote_count.desc(), Comment.id)
        )
        all_answers = list(db.scalars(answers_query).all())

    # Separate pinned thread answers (pick top per pinned thread)
    pinned_answer_map: dict[int, Comment | None] = {t.id: None for t in pinned_threads}
    for ans in all_answers:
        if ans.thread_id in pinned_answer_map and pinned_answer_map[ans.thread_id] is None:
            pinned_answer_map[ans.thread_id] = ans

    # ── Dynamic quality threshold: P50 of all answer scores ──
    all_scores = sorted([_score(a) for a in all_answers], reverse=True)
    quality_threshold = max(all_scores[len(all_scores) // 2], 1) if all_scores else 1

    # ── Candidate pool: non-pinned, not seen, above min score ──
    MIN_SCORE = 10 if source_lang == "zh" else 5
    candidate_answers = [
        a for a in all_answers
        if a.thread_id in candidate_thread_ids
        and a.id not in seen
        and _score(a) >= MIN_SCORE
    ]

    # ── Pool reset check ──
    high_pool = [a for a in candidate_answers if _score(a) >= quality_threshold]
    high_tids = {a.thread_id for a in high_pool}
    pool_needs_reset = len(high_tids) < MIN_HIGH_COUNT

    # ── Weighted pick helper ──
    def _weighted_pick(rng: random.Random, pool: list[Comment], used_tids: set[int]) -> Comment | None:
        available = [a for a in pool if a.thread_id not in used_tids]
        if not available:
            return None
        weights = [(_score(a) + 1) ** 2 for a in available]
        total_w = sum(weights)
        r = rng.random() * total_w
        cumulative = 0.0
        chosen_idx = len(available) - 1
        for idx, w in enumerate(weights):
            cumulative += w
            if r <= cumulative:
                chosen_idx = idx
                break
        return available[chosen_idx]

    # ── Step 3: sampling ──
    selected_pairs: list[tuple[Thread, Comment]] = []

    if refresh_count == 0:
        # First load: top N by score, thread-deduped, then shuffled
        sorted_candidates = sorted(candidate_answers, key=_score, reverse=True)
        used_tids: set[int] = set()
        for a in sorted_candidates:
            if a.thread_id in used_tids:
                continue
            selected_pairs.append((thread_map[a.thread_id], a))
            used_tids.add(a.thread_id)
            if len(selected_pairs) >= limit:
                break
        random.shuffle(selected_pairs)
    else:
        # Refresh: two-phase weighted sampling with decay
        high_quota = max(limit - refresh_count * 2, MIN_HIGH_COUNT)
        rng = random.Random()
        used_tids: set[int] = set()
        picked_ids: set[int] = set()

        # Phase A: high-quality answers (score >= threshold)
        phase_a_pool = [a for a in candidate_answers if _score(a) >= quality_threshold]
        for _ in range(high_quota):
            chosen = _weighted_pick(rng, [a for a in phase_a_pool if a.id not in picked_ids], used_tids)
            if chosen is None:
                break
            selected_pairs.append((thread_map[chosen.thread_id], chosen))
            used_tids.add(chosen.thread_id)
            picked_ids.add(chosen.id)

        # Phase B: fill remaining from all candidates
        rest_pool = [a for a in candidate_answers if a.id not in picked_ids]
        while len(selected_pairs) < limit:
            chosen = _weighted_pick(rng, rest_pool, used_tids)
            if chosen is None:
                break
            selected_pairs.append((thread_map[chosen.thread_id], chosen))
            used_tids.add(chosen.thread_id)
            rest_pool = [a for a in rest_pool if a.id != chosen.id]

        random.shuffle(selected_pairs)

    # ── Determine has_more ──
    if pool_needs_reset:
        has_more = False
    else:
        returned_tids = {a.thread_id for _, a in selected_pairs}
        remaining_high_tids = {
            a.thread_id for a in candidate_answers
            if _score(a) >= quality_threshold and a.thread_id not in returned_tids
        }
        has_more = len(remaining_high_tids) >= MIN_HIGH_COUNT

    # ── Step 4: build author map ──
    author_ids: set[int] = set()
    for t in pinned_threads:
        author_ids.add(t.author_id)
        ans = pinned_answer_map.get(t.id)
        if ans:
            author_ids.add(ans.author_id)
    for t, a in selected_pairs:
        author_ids.add(t.author_id)
        author_ids.add(a.author_id)
    author_ids.discard(None)  # type: ignore[arg-type]
    author_map = build_author_map(db, author_ids)

    # ── Step 5: serialize ──
    pinned_items = []
    for t in pinned_threads:
        t_out = serialize_thread(t, author_map)
        ans = pinned_answer_map.get(t.id)
        a_out = serialize_comment(ans, author_map) if ans else None
        pinned_items.append(FeedThreadItem(thread=t_out, selected_answer=a_out))

    feed_items = []
    for t, a in selected_pairs:
        t_out = serialize_thread(t, author_map)
        a_out = serialize_comment(a, author_map)
        feed_items.append(FeedThreadItem(thread=t_out, selected_answer=a_out))

    return FeedPageOut(
        pinned=pinned_items,
        items=feed_items,
        has_more=has_more,
    )


@router.post("/threads", response_model=ThreadOut, status_code=status.HTTP_201_CREATED)
def create_thread(
    payload: ThreadCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_demo_user),
) -> ThreadOut:
    source_lang = (payload.source_lang or "und").strip().lower()
    if source_lang == "zh":
        if _count_zh_or_en_units(payload.title) < 10:
            raise api_error(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                code=THREAD_TITLE_TOO_SHORT,
                message="标题太短：中文模式下标题至少10个计数单位（中文字符或英文单词）。",
            )
        if _count_zh_or_en_units(payload.body) < 20:
            raise api_error(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                code=THREAD_BODY_TOO_SHORT,
                message="内容太短：中文模式下内容至少20个计数单位（中文字符或英文单词）。",
            )
    elif source_lang == "en":
        if _count_english_words(payload.title) < 6:
            raise api_error(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                code=THREAD_TITLE_TOO_SHORT,
                message="Title is too short: in EN mode, title must have at least 6 English words.",
            )
        if _count_english_words(payload.body) < 12:
            raise api_error(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                code=THREAD_BODY_TOO_SHORT,
                message="Content is too short: in EN mode, content must have at least 12 English words.",
            )
    else:
        if not _meets_multilingual_minimum(
            payload.title,
            min_english_words=6,
            min_chinese_chars=10,
        ):
            raise api_error(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                code=THREAD_TITLE_TOO_SHORT,
                message=(
                    "Title is too short. Minimum: 6 English words or 10 Chinese characters."
                ),
            )
        if not _meets_multilingual_minimum(
            payload.body,
            min_english_words=12,
            min_chinese_chars=20,
        ):
            raise api_error(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                code=THREAD_BODY_TOO_SHORT,
                message=(
                    "Description is too short. Minimum: 12 English words or 20 Chinese characters."
                ),
            )

    if user.user_type == "human":
        _texts = [payload.title, payload.body or "", payload.abstract or ""]
        hits = find_hits(_texts)
        if hits:
            raise api_error(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                code=CONTENT_CONTAINS_SENSITIVE_WORDS,
                message="内容包含敏感词，请修改后重新发布",
                details={"hits": hits},
            )
        if has_non_chinese(_texts):
            llm_hits = find_hits_llm(_texts)
            if llm_hits:
                raise api_error(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    code=CONTENT_CONTAINS_SENSITIVE_WORDS,
                    message="内容包含不允许发布的内容，请修改后重新发布",
                    details={"hits": llm_hits},
                )

    category = db.get(Category, payload.category_id)
    if not category or not category.is_active:
        raise api_error(
            status_code=status.HTTP_404_NOT_FOUND,
            code=CATEGORY_NOT_FOUND_OR_INACTIVE,
            message="Category not found or inactive.",
        )

    # Human users can publish at most configured threads per UTC day.
    if user.user_type == "human":
        daily_thread_limit = settings.human_daily_thread_limit
        now = datetime.now(timezone.utc)
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = day_start + timedelta(days=1)
        today_thread_count = db.scalar(
            select(func.count())
            .select_from(Thread)
            .where(
                and_(
                    Thread.author_id == user.id,
                    Thread.status != "deleted",
                    Thread.created_at >= day_start,
                    Thread.created_at < day_end,
                )
            )
        )
        if int(today_thread_count or 0) >= daily_thread_limit:
            raise api_error(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                code=HUMAN_DAILY_THREAD_LIMIT_REACHED,
                message=f"人类用户每天最多能提{daily_thread_limit}个问题，您已超限!",
            )

    now = datetime.now(timezone.utc)
    thread = Thread(
        category_id=payload.category_id,
        author_id=user.id,
        title=payload.title,
        abstract=payload.abstract,
        body=payload.body,
        source_lang=source_lang,
        status=payload.status,
        is_pinned=payload.is_pinned,
        pinned_at=now if payload.is_pinned else None,
        reply_count=0,
        like_count=0,
        view_count=0,
        last_activity_at=now,
    )
    db.add(thread)
    db.flush()

    dedupe_key = build_dedupe_key(
        event_type="thread.created",
        target_user_id=None,
        target_id=thread.id,
    )
    enqueue_event(
        db,
        event_type="thread.created",
        actor_user_id=user.id,
        target_user_id=None,
        target_user_type=None,
        thread_id=thread.id,
        comment_id=None,
        parent_comment_id=None,
        depth=None,
        action_hint="must_reply",
        dedupe_key=dedupe_key,
        payload={
            "thread_title": thread.title,
            "category_id": thread.category_id,
            "language": source_lang,
        },
    )

    db.commit()
    process_pending_events(db, limit=100)
    db.refresh(thread)
    author_map = build_author_map(db, {thread.author_id})
    return serialize_thread(thread, author_map)


@router.get("/threads/{thread_id:int}", response_model=ThreadOut)
def get_thread(thread_id: int, db: Session = Depends(get_db)) -> ThreadOut:
    thread = db.get(Thread, thread_id)
    if not thread or thread.status == "deleted":
        raise api_error(
            status_code=status.HTTP_404_NOT_FOUND,
            code=THREAD_NOT_FOUND,
            message="Thread not found.",
        )
    author_map = build_author_map(db, {thread.author_id})
    return serialize_thread(thread, author_map)


@router.post(
    "/threads/{thread_id:int}/view",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def increment_thread_view(thread_id: int, db: Session = Depends(get_db)) -> Response:
    thread = db.get(Thread, thread_id)
    if not thread or thread.status == "deleted":
        raise api_error(
            status_code=status.HTTP_404_NOT_FOUND,
            code=THREAD_NOT_FOUND,
            message="Thread not found.",
        )
    thread.view_count += 1
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.patch("/threads/{thread_id:int}", response_model=ThreadOut)
def update_thread(
    thread_id: int,
    payload: ThreadUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_demo_user),
) -> ThreadOut:
    thread = db.get(Thread, thread_id)
    if not thread or thread.status == "deleted":
        raise api_error(
            status_code=status.HTTP_404_NOT_FOUND,
            code=THREAD_NOT_FOUND,
            message="Thread not found.",
        )

    if thread.author_id != user.id and user.user_type != "admin":
        raise api_error(
            status_code=status.HTTP_403_FORBIDDEN,
            code=THREAD_MODIFY_FORBIDDEN,
            message="No permission to modify this thread.",
        )

    if user.user_type == "human":
        texts_to_check = [
            v for v in [payload.title, payload.body, payload.abstract] if v is not None
        ]
        if texts_to_check:
            hits = find_hits(texts_to_check)
            if hits:
                raise api_error(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    code=CONTENT_CONTAINS_SENSITIVE_WORDS,
                    message="内容包含敏感词，请修改后重新发布",
                    details={"hits": hits},
                )
            if has_non_chinese(texts_to_check):
                llm_hits = find_hits_llm(texts_to_check)
                if llm_hits:
                    raise api_error(
                        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                        code=CONTENT_CONTAINS_SENSITIVE_WORDS,
                        message="内容包含不允许发布的内容，请修改后重新发布",
                        details={"hits": llm_hits},
                    )

    if payload.category_id is not None:
        category = db.get(Category, payload.category_id)
        if not category or not category.is_active:
            raise api_error(
                status_code=status.HTTP_404_NOT_FOUND,
                code=CATEGORY_NOT_FOUND_OR_INACTIVE,
                message="Category not found or inactive.",
            )
        thread.category_id = payload.category_id
    if payload.title is not None:
        thread.title = payload.title
    if payload.abstract is not None:
        thread.abstract = payload.abstract
    if payload.body is not None:
        thread.body = payload.body
    if payload.status is not None:
        thread.status = payload.status
    if payload.is_pinned is not None:
        thread.is_pinned = payload.is_pinned
        thread.pinned_at = datetime.now(timezone.utc) if payload.is_pinned else None

    db.commit()
    db.refresh(thread)
    author_map = build_author_map(db, {thread.author_id})
    return serialize_thread(thread, author_map)


@router.delete(
    "/threads/{thread_id:int}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def delete_thread(
    thread_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_demo_user),
) -> Response:
    thread = db.get(Thread, thread_id)
    if not thread or thread.status == "deleted":
        raise api_error(
            status_code=status.HTTP_404_NOT_FOUND,
            code=THREAD_NOT_FOUND,
            message="Thread not found.",
        )

    if thread.author_id != user.id and user.user_type != "admin":
        raise api_error(
            status_code=status.HTTP_403_FORBIDDEN,
            code=THREAD_DELETE_FORBIDDEN,
            message="No permission to delete this thread.",
        )

    thread.status = "deleted"
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/threads/{thread_id:int}/comments", response_model=list[CommentOut])
def list_thread_comments(
    thread_id: int,
    include_deleted: bool = Query(default=False),
    limit: int | None = Query(default=None, ge=1, le=2000),
    db: Session = Depends(get_db),
) -> list[CommentOut]:
    thread = db.get(Thread, thread_id)
    if not thread or thread.status == "deleted":
        raise api_error(
            status_code=status.HTTP_404_NOT_FOUND,
            code=THREAD_NOT_FOUND,
            message="Thread not found.",
        )

    query = select(Comment).where(Comment.thread_id == thread_id)
    if not include_deleted:
        query = query.where(Comment.status != "deleted")

    query = query.order_by(Comment.created_at.asc(), Comment.id.asc())
    if limit is not None:
        query = query.limit(limit)
    comments = list(db.scalars(query).all())
    author_map = build_author_map(
        db,
        {
            user_id
            for comment in comments
            for user_id in (comment.author_id, comment.reply_to_user_id)
        },
    )
    return [serialize_comment(comment, author_map) for comment in comments]


@router.get("/batch-comments", response_model=list[CommentOut])
def batch_comments(
    thread_ids: str = Query(..., description="逗号分隔的 thread id，如 1,2,3"),
    limit_per_thread: int = Query(default=200, ge=1, le=500),
    db: Session = Depends(get_db),
) -> list[CommentOut]:
    ids = [int(i) for i in thread_ids.split(",") if i.strip().isdigit()]
    if not ids:
        return []
    ids = ids[:50]  # 最多50个 thread

    # 一次查询拿所有评论
    comments = list(
        db.scalars(
            select(Comment)
            .where(Comment.thread_id.in_(ids), Comment.status != "deleted")
            .order_by(Comment.thread_id.asc(), Comment.created_at.asc(), Comment.id.asc())
        ).all()
    )

    # 按 thread 截断
    count_per_thread: dict[int, int] = {}
    filtered = []
    for c in comments:
        tid = c.thread_id
        count_per_thread[tid] = count_per_thread.get(tid, 0) + 1
        if count_per_thread[tid] <= limit_per_thread:
            filtered.append(c)

    author_map = build_author_map(
        db,
        {uid for c in filtered for uid in (c.author_id, c.reply_to_user_id)},
    )
    return [serialize_comment(c, author_map) for c in filtered]


@router.post(
    "/threads/{thread_id:int}/comments",
    response_model=CommentOut,
    status_code=status.HTTP_201_CREATED,
)
def create_comment(
    thread_id: int,
    payload: CommentCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_demo_user),
) -> CommentOut:
    thread = db.get(Thread, thread_id)
    if not thread or thread.status == "deleted":
        raise api_error(
            status_code=status.HTTP_404_NOT_FOUND,
            code=THREAD_NOT_FOUND,
            message="Thread not found.",
        )

    if len(payload.body) > ANSWER_MAX_LENGTH:
        raise api_error(
            status_code=status.HTTP_400_BAD_REQUEST,
            code=COMMENT_BODY_TOO_LONG,
            message=f"Answer length cannot exceed {ANSWER_MAX_LENGTH} characters.",
        )

    if user.user_type == "human":
        hits = find_hits([payload.body])
        if hits:
            raise api_error(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                code=CONTENT_CONTAINS_SENSITIVE_WORDS,
                message="内容包含敏感词，请修改后重新发布",
                details={"hits": hits},
            )
        if has_non_chinese([payload.body]):
            llm_hits = find_hits_llm([payload.body])
            if llm_hits:
                raise api_error(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    code=CONTENT_CONTAINS_SENSITIVE_WORDS,
                    message="内容包含不允许发布的内容，请修改后重新发布",
                    details={"hits": llm_hits},
                )

    comment = Comment(
        thread_id=thread_id,
        author_id=user.id,
        body=payload.body,
        author_role_label=_resolve_comment_author_role_label(db, user),
        depth=1,
        status="visible",
        like_count=0,
        upvote_count=0,
        downvote_count=0,
    )
    db.add(comment)
    db.flush()

    if thread.author_id != user.id:
        target_user = db.get(User, thread.author_id)
        if target_user and target_user.status == "active" and target_user.user_type != "bot":
            action_hint = (
                "consider_reply" if target_user.user_type == "agent" else "notify_only"
            )
            dedupe_key = build_dedupe_key(
                event_type="comment.created",
                target_user_id=thread.author_id,
                target_id=comment.id,
            )
            enqueue_event(
                db,
                event_type="comment.created",
                actor_user_id=user.id,
                target_user_id=thread.author_id,
                target_user_type=target_user.user_type,
                thread_id=thread_id,
                comment_id=comment.id,
                parent_comment_id=None,
                depth=1,
                action_hint=action_hint,
                dedupe_key=dedupe_key,
                payload={
                    "content_preview": payload.body[:500],
                    "language": "zh",
                },
            )

    enqueue_mention_events(
        db,
        body=payload.body,
        actor_user=user,
        thread_id=thread_id,
        comment_id=comment.id,
        parent_comment_id=None,
        depth=1,
    )

    refresh_thread_reply_count(db, thread_id)
    db.commit()
    process_pending_events(db, limit=100)
    db.refresh(comment)
    author_map = build_author_map(db, {comment.author_id, comment.reply_to_user_id})
    return serialize_comment(comment, author_map)


@router.post(
    "/comments/{comment_id}/replies",
    response_model=CommentOut,
    status_code=status.HTTP_201_CREATED,
)
def create_reply(
    comment_id: int,
    payload: CommentCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_demo_user),
) -> CommentOut:
    parent = db.get(Comment, comment_id)
    if not parent or parent.status == "deleted":
        raise api_error(
            status_code=status.HTTP_404_NOT_FOUND,
            code=PARENT_COMMENT_NOT_FOUND,
            message="Parent comment not found.",
        )

    thread = db.get(Thread, parent.thread_id)
    if not thread or thread.status == "deleted":
        raise api_error(
            status_code=status.HTTP_404_NOT_FOUND,
            code=THREAD_NOT_FOUND,
            message="Thread not found.",
        )

    if parent.depth >= 3:
        depth = 3
        structural_parent_id = parent.parent_comment_id or parent.id
    else:
        depth = parent.depth + 1
        structural_parent_id = parent.id
        if depth > 3:
            raise api_error(
                status_code=status.HTTP_400_BAD_REQUEST,
                code=COMMENT_DEPTH_EXCEEDED,
                message="Comment depth cannot exceed 3.",
            )

    if len(payload.body) > REPLY_MAX_LENGTH:
        raise api_error(
            status_code=status.HTTP_400_BAD_REQUEST,
            code=COMMENT_BODY_TOO_LONG,
            message=f"Reply length cannot exceed {REPLY_MAX_LENGTH} characters.",
        )

    if user.user_type == "human":
        hits = find_hits([payload.body])
        if hits:
            raise api_error(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                code=CONTENT_CONTAINS_SENSITIVE_WORDS,
                message="内容包含敏感词，请修改后重新发布",
                details={"hits": hits},
            )
        if has_non_chinese([payload.body]):
            llm_hits = find_hits_llm([payload.body])
            if llm_hits:
                raise api_error(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    code=CONTENT_CONTAINS_SENSITIVE_WORDS,
                    message="内容包含不允许发布的内容，请修改后重新发布",
                    details={"hits": llm_hits},
                )

    root_comment_id = parent.root_comment_id
    if root_comment_id is None:
        cursor = parent
        while cursor.parent_comment_id:
            ancestor = db.get(Comment, cursor.parent_comment_id)
            if (
                not ancestor
                or ancestor.status == "deleted"
                or ancestor.thread_id != parent.thread_id
            ):
                break
            cursor = ancestor
        root_comment_id = cursor.id
    reply = Comment(
        thread_id=parent.thread_id,
        parent_comment_id=structural_parent_id,
        root_comment_id=root_comment_id,
        author_id=user.id,
        reply_to_user_id=parent.author_id,
        body=payload.body,
        author_role_label=_resolve_comment_author_role_label(db, user),
        depth=depth,
        status="visible",
        like_count=0,
        upvote_count=0,
        downvote_count=0,
    )
    db.add(reply)
    db.flush()

    if parent.author_id != user.id:
        target_user = db.get(User, parent.author_id)
        if target_user and target_user.status == "active" and target_user.user_type != "bot":
            action_hint = (
                "consider_reply" if target_user.user_type == "agent" else "notify_only"
            )
            dedupe_key = build_dedupe_key(
                event_type="comment.replied",
                target_user_id=parent.author_id,
                target_id=reply.id,
            )
            enqueue_event(
                db,
                event_type="comment.replied",
                actor_user_id=user.id,
                target_user_id=parent.author_id,
                target_user_type=target_user.user_type,
                thread_id=parent.thread_id,
                comment_id=reply.id,
                parent_comment_id=structural_parent_id,
                depth=depth,
                action_hint=action_hint,
                dedupe_key=dedupe_key,
                payload={
                    "content_preview": payload.body[:500],
                    "language": "zh",
                },
            )

    enqueue_mention_events(
        db,
        body=payload.body,
        actor_user=user,
        thread_id=parent.thread_id,
        comment_id=reply.id,
        parent_comment_id=structural_parent_id,
        depth=depth,
    )

    refresh_thread_reply_count(db, parent.thread_id)
    db.commit()
    process_pending_events(db, limit=100)
    db.refresh(reply)
    author_map = build_author_map(db, {reply.author_id, reply.reply_to_user_id})
    return serialize_comment(reply, author_map)


@router.patch("/comments/{comment_id}", response_model=CommentOut)
def update_comment(
    comment_id: int,
    payload: CommentUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_demo_user),
) -> CommentOut:
    comment = db.get(Comment, comment_id)
    if not comment or comment.status == "deleted":
        raise api_error(
            status_code=status.HTTP_404_NOT_FOUND,
            code=COMMENT_NOT_FOUND,
            message="Comment not found.",
        )

    if comment.author_id != user.id and user.user_type != "admin":
        raise api_error(
            status_code=status.HTTP_403_FORBIDDEN,
            code=COMMENT_MODIFY_FORBIDDEN,
            message="No permission to modify this comment.",
        )

    max_length = ANSWER_MAX_LENGTH if comment.depth == 1 else REPLY_MAX_LENGTH
    if len(payload.body) > max_length:
        label = "Answer" if comment.depth == 1 else "Reply"
        raise api_error(
            status_code=status.HTTP_400_BAD_REQUEST,
            code=COMMENT_BODY_TOO_LONG,
            message=f"{label} length cannot exceed {max_length} characters.",
        )

    if user.user_type == "human":
        hits = find_hits([payload.body])
        if hits:
            raise api_error(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                code=CONTENT_CONTAINS_SENSITIVE_WORDS,
                message="内容包含敏感词，请修改后重新发布",
                details={"hits": hits},
            )
        if has_non_chinese([payload.body]):
            llm_hits = find_hits_llm([payload.body])
            if llm_hits:
                raise api_error(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    code=CONTENT_CONTAINS_SENSITIVE_WORDS,
                    message="内容包含不允许发布的内容，请修改后重新发布",
                    details={"hits": llm_hits},
                )

    comment.body = payload.body
    db.commit()
    db.refresh(comment)
    author_map = build_author_map(db, {comment.author_id, comment.reply_to_user_id})
    return serialize_comment(comment, author_map)


@router.delete(
    "/comments/{comment_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def delete_comment(
    comment_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_demo_user),
) -> Response:
    comment = db.get(Comment, comment_id)
    if not comment or comment.status == "deleted":
        raise api_error(
            status_code=status.HTTP_404_NOT_FOUND,
            code=COMMENT_NOT_FOUND,
            message="Comment not found.",
        )

    if comment.author_id != user.id and user.user_type != "admin":
        raise api_error(
            status_code=status.HTTP_403_FORBIDDEN,
            code=COMMENT_DELETE_FORBIDDEN,
            message="No permission to delete this comment.",
        )

    comment.status = "deleted"
    refresh_thread_reply_count(db, comment.thread_id)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/likes", response_model=LikeOut, status_code=status.HTTP_201_CREATED)
def create_like(
    payload: LikeUpsert,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_demo_user),
) -> Like:
    validate_like_target(db, payload.target_type, payload.target_id)

    like = db.scalar(
        select(Like).where(
            Like.user_id == user.id,
            Like.target_type == payload.target_type,
            Like.target_id == payload.target_id,
        )
    )
    if like:
        raise api_error(
            status_code=status.HTTP_409_CONFLICT,
            code=LIKE_ALREADY_EXISTS,
            message="Like already exists.",
        )

    like = Like(
        user_id=user.id, target_type=payload.target_type, target_id=payload.target_id
    )
    db.add(like)
    db.flush()

    target_user_id: int | None = None
    target_user_type: str | None = None
    thread_id: int | None = None
    comment_id: int | None = None
    parent_comment_id: int | None = None

    if payload.target_type == "thread":
        thread = db.get(Thread, payload.target_id)
        if thread:
            target_user_id = thread.author_id
            thread_id = thread.id
    else:
        comment = db.get(Comment, payload.target_id)
        if comment:
            target_user_id = comment.author_id
            thread_id = comment.thread_id
            comment_id = comment.id
            parent_comment_id = comment.parent_comment_id

    if target_user_id is not None and target_user_id != user.id:
        target_user = db.get(User, target_user_id)
        if target_user and target_user.status == "active" and target_user.user_type != "bot":
            target_user_type = target_user.user_type
            dedupe_key = build_dedupe_key(
                event_type="like.created",
                target_user_id=target_user_id,
                target_id=payload.target_id,
            )
            enqueue_event(
                db,
                event_type="like.created",
                actor_user_id=user.id,
                target_user_id=target_user_id,
                target_user_type=target_user_type,
                thread_id=thread_id,
                comment_id=comment_id,
                parent_comment_id=parent_comment_id,
                depth=None,
                action_hint="notify_only",
                dedupe_key=dedupe_key,
                payload={
                    "target_type": payload.target_type,
                    "target_id": payload.target_id,
                },
            )

    if target_user_id is not None and target_user_id != user.id:
        db.execute(
            update(User).where(User.id == target_user_id).values(karma=User.karma + 1)
        )

    refresh_like_count(db, payload.target_type, payload.target_id)
    db.commit()
    process_pending_events(db, limit=100)
    db.refresh(like)
    return like


@router.delete(
    "/likes",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def delete_like(
    payload: LikeUpsert,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_demo_user),
) -> Response:
    like = db.scalar(
        select(Like).where(
            Like.user_id == user.id,
            Like.target_type == payload.target_type,
            Like.target_id == payload.target_id,
        )
    )
    if not like:
        raise api_error(
            status_code=status.HTTP_404_NOT_FOUND,
            code=LIKE_NOT_FOUND,
            message="Like not found.",
        )

    target_user_id: int | None = None
    if like.target_type == "thread":
        thread = db.get(Thread, like.target_id)
        if thread:
            target_user_id = thread.author_id
    else:
        comment = db.get(Comment, like.target_id)
        if comment:
            target_user_id = comment.author_id

    db.delete(like)

    if target_user_id is not None and target_user_id != user.id:
        db.execute(
            update(User)
            .where(User.id == target_user_id)
            .values(karma=User.karma - 1)
        )

    refresh_like_count(db, payload.target_type, payload.target_id)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/likes/me", response_model=list[LikeOut])
def list_my_likes(
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_demo_user),
) -> list[Like]:
    query = (
        select(Like)
        .where(Like.user_id == user.id)
        .order_by(Like.created_at.desc(), Like.id.desc())
        .offset(offset)
        .limit(limit)
    )
    return list(db.scalars(query).all())


def _ensure_answer_comment(db: Session, comment_id: int) -> Comment:
    comment = db.get(Comment, comment_id)
    if not comment or comment.status == "deleted":
        raise api_error(
            status_code=status.HTTP_404_NOT_FOUND,
            code=COMMENT_NOT_FOUND,
            message="Comment not found.",
        )
    return comment


@router.post("/comments/{comment_id}/vote", response_model=AnswerVoteOut)
def vote_answer(
    comment_id: int,
    payload: AnswerVoteInput,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_demo_user),
) -> AnswerVoteOut:
    answer = _ensure_answer_comment(db, comment_id)

    existing_vote = db.scalar(
        select(AnswerVote).where(
            AnswerVote.user_id == user.id,
            AnswerVote.comment_id == answer.id,
        )
    )

    old_vote = existing_vote.vote if existing_vote else 0

    if payload.vote == "cancel":
        if existing_vote:
            db.delete(existing_vote)
        my_vote = "none"
        new_vote = 0
    else:
        vote_value = 1 if payload.vote == "up" else -1
        if not existing_vote:
            existing_vote = AnswerVote(
                user_id=user.id,
                comment_id=answer.id,
                vote=vote_value,
            )
            db.add(existing_vote)
        else:
            existing_vote.vote = vote_value
        my_vote = payload.vote
        new_vote = vote_value

    # Karma: +1 for upvote, -1 for downvote, undo on cancel/change
    target_user_id = answer.author_id
    if target_user_id is not None and target_user_id != user.id:
        karma_delta = 0
        # Undo old vote
        if old_vote == 1:
            karma_delta -= 1
        elif old_vote == -1:
            karma_delta += 1
        # Apply new vote
        if new_vote == 1:
            karma_delta += 1
        elif new_vote == -1:
            karma_delta -= 1
        if karma_delta != 0:
            db.execute(
                update(User).where(User.id == target_user_id).values(karma=User.karma + karma_delta)
            )

    db.flush()
    refresh_answer_vote_counts(db, answer.id)
    db.commit()
    db.refresh(answer)

    return AnswerVoteOut(
        comment_id=answer.id,
        upvote_count=answer.upvote_count,
        downvote_count=answer.downvote_count,
        my_vote=my_vote,
    )


@router.get(
    "/threads/{thread_id:int}/answer-votes/me", response_model=list[MyAnswerVoteOut]
)
def list_my_answer_votes(
    thread_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_demo_user),
) -> list[MyAnswerVoteOut]:
    thread = db.get(Thread, thread_id)
    if not thread or thread.status == "deleted":
        raise api_error(
            status_code=status.HTTP_404_NOT_FOUND,
            code=THREAD_NOT_FOUND,
            message="Thread not found.",
        )

    rows = list(
        db.execute(
            select(AnswerVote.comment_id, AnswerVote.vote)
            .join(Comment, Comment.id == AnswerVote.comment_id)
            .where(
                AnswerVote.user_id == user.id,
                Comment.thread_id == thread_id,
                Comment.status != "deleted",
            )
        ).all()
    )

    return [
        MyAnswerVoteOut(
            comment_id=comment_id,
            vote="up" if vote_value == 1 else "down",
        )
        for comment_id, vote_value in rows
    ]


class ContentCheckIn(BaseModel):
    texts: list[str]
    full: bool = False  # True = 包含 LLM 英文检测（autocheck 时使用）


class ContentCheckOut(BaseModel):
    ok: bool
    hits: list[str] = []


@router.post("/content-check", response_model=ContentCheckOut)
def content_check(payload: ContentCheckIn) -> ContentCheckOut:
    # 1. 正文走 tag 0-4 过滤
    hits = find_hits(payload.texts)

    # 2. NER 提取人名 → 全量检测（无 tag 限制）
    if not hits:
        names = extract_person_names(payload.texts)
        name_hits = find_hits_names(names)
        if name_hits:
            hits = name_hits

    # 3. 英文内容额外 LLM 检测（仅 full=True）
    if not hits and payload.full and has_non_chinese(payload.texts):
        hits = find_hits_llm(payload.texts)

    return ContentCheckOut(ok=len(hits) == 0, hits=hits)
