from __future__ import annotations

import io
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Response, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.api.deps.auth import get_bot_user, get_current_demo_user
from app.api.v1.shared import AuthorSummaryOut, build_author_map
from app.core.error_codes import (
    BOT_ALREADY_EXISTS,
    BOT_NOT_FOUND,
    CATEGORY_NOT_FOUND_OR_INACTIVE,
    COMMENT_DELETE_FORBIDDEN,
    COMMENT_DEPTH_EXCEEDED,
    COMMENT_NOT_FOUND,
    FOLLOW_ALREADY_EXISTS,
    FOLLOW_NOT_FOUND,
    FOLLOW_SELF_NOT_ALLOWED,
    LIKE_ALREADY_EXISTS,
    LIKE_NOT_FOUND,
    INVALID_LIKE_TARGET_TYPE,
    PARENT_COMMENT_NOT_FOUND,
    THREAD_DELETE_FORBIDDEN,
    THREAD_NOT_FOUND,
    USER_NOT_FOUND,
    DM_PEER_NOT_FOUND,
    DM_SELF_CHAT_NOT_ALLOWED,
)
from app.core.errors import api_error
from app.db.session import get_db
from app.models.bot import Bot, generate_bot_api_key
from app.models.dm import DMConversation, DMMessage, DMParticipant, DMPeerPair
from app.models.forum import AnswerVote, Category, Comment, Like, Thread
from app.models.prediction import PredictionMarket, PredictionOption, PredictionVote
from app.models.user import User, UserFollow, UserType
from app.services.forum_metrics import (
    refresh_answer_vote_counts,
    refresh_like_count,
    refresh_thread_reply_count,
)

router = APIRouter(prefix="/bot", tags=["bot"])


# ---------------------------------------------------------------------------
# Shared output schemas (reuse where possible)
# ---------------------------------------------------------------------------


class BotOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    is_enabled: bool
    label: str | None = None
    last_used_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class BotWithKeyOut(BotOut):
    api_key: str


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
    reply_count: int
    like_count: int
    view_count: int
    last_activity_at: datetime
    created_at: datetime
    updated_at: datetime
    author: AuthorSummaryOut | None = None
    summary: str | None = None
    via_bot: bool = False


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
    created_at: datetime
    updated_at: datetime
    author: AuthorSummaryOut | None = None
    via_bot: bool = False


class CategoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    slug: str
    description: str | None = None
    sort_order: int
    is_active: bool


class LikeOut(BaseModel):
    id: int
    user_id: int
    target_type: str
    target_id: int
    created_at: datetime


class AnswerVoteOut(BaseModel):
    comment_id: int
    upvote_count: int
    downvote_count: int
    my_vote: Literal["up", "down", "none"]


class FollowOut(BaseModel):
    follower_user_id: int
    followee_user_id: int


class DMSendOut(BaseModel):
    conversation_id: int
    message_id: int
    body: str
    created_at: datetime


class UserProfileOut(BaseModel):
    id: int
    username: str
    display_name: str
    bio: str | None = None
    user_type: str
    avatar_url: str
    is_verified: bool
    status: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _serialize_thread(thread: Thread, author_map: dict[int, AuthorSummaryOut]) -> ThreadOut:
    return ThreadOut(
        id=thread.id,
        category_id=thread.category_id,
        author_id=thread.author_id,
        title=thread.title,
        abstract=thread.abstract,
        body=thread.body,
        status=thread.status,
        is_pinned=thread.is_pinned,
        reply_count=thread.reply_count,
        like_count=thread.like_count,
        view_count=thread.view_count,
        last_activity_at=thread.last_activity_at,
        created_at=thread.created_at,
        updated_at=thread.updated_at,
        author=author_map.get(thread.author_id),
        summary=thread.summary,
        via_bot=thread.via_bot,
    )


def _serialize_comment(comment: Comment, author_map: dict[int, AuthorSummaryOut]) -> CommentOut:
    return CommentOut(
        id=comment.id,
        thread_id=comment.thread_id,
        parent_comment_id=comment.parent_comment_id,
        root_comment_id=comment.root_comment_id,
        author_id=comment.author_id,
        reply_to_user_id=comment.reply_to_user_id,
        body=comment.body,
        depth=comment.depth,
        status=comment.status,
        like_count=comment.like_count,
        upvote_count=comment.upvote_count,
        downvote_count=comment.downvote_count,
        created_at=comment.created_at,
        updated_at=comment.updated_at,
        author=author_map.get(comment.author_id),
        via_bot=comment.via_bot,
    )


# ---------------------------------------------------------------------------
# Mode A: Standalone bot self-registration (no JWT required)
# ---------------------------------------------------------------------------


class BotRegisterIn(BaseModel):
    username: str = Field(min_length=3, max_length=50)
    display_name: str = Field(min_length=1, max_length=64)
    label: str | None = Field(default=None, max_length=64)


class BotRegisterOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    api_key: str
    user_id: int
    username: str
    display_name: str


@router.post("/register", response_model=BotRegisterOut, status_code=status.HTTP_201_CREATED)
def register_bot(
    payload: BotRegisterIn,
    db: Session = Depends(get_db),
) -> BotRegisterOut:
    """
    Mode A: Register a standalone bot identity (user_type='bot').
    No human account required. Returns an api_key to use with X-Api-Key header.
    """
    from app.models.user import build_default_avatar_url
    from sqlalchemy.exc import IntegrityError

    existing = db.scalar(select(User).where(User.username == payload.username))
    if existing:
        raise api_error(
            status_code=status.HTTP_409_CONFLICT,
            code=BOT_ALREADY_EXISTS,
            message=f"Username '{payload.username}' is already taken.",
        )

    bot_user = User(
        user_type=UserType.BOT.value,
        username=payload.username,
        display_name=payload.display_name,
        avatar_url=build_default_avatar_url(payload.username),
        status="active",
    )
    db.add(bot_user)
    db.flush()

    bot = Bot(
        user_id=bot_user.id,
        owner_user_id=None,
        api_key=generate_bot_api_key(),
        is_enabled=True,
        label=payload.label or payload.display_name,
    )
    db.add(bot)
    db.commit()
    db.refresh(bot_user)

    return BotRegisterOut(
        api_key=bot.api_key,
        user_id=bot_user.id,
        username=bot_user.username,
        display_name=bot_user.display_name,
    )


# ---------------------------------------------------------------------------
# Bot management endpoints (JWT auth)
# ---------------------------------------------------------------------------


class BotUpdateIn(BaseModel):
    is_enabled: bool | None = None
    label: str | None = Field(default=None, max_length=64)


@router.get("/me", response_model=BotWithKeyOut)
def get_my_bot(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_demo_user),
) -> BotWithKeyOut:
    bot = db.scalar(select(Bot).where(Bot.user_id == current_user.id))
    if not bot:
        # Lazy init for users registered before this feature
        bot = Bot(
            user_id=current_user.id,
            api_key=generate_bot_api_key(),
            label=current_user.display_name,
            is_enabled=False,
        )
        db.add(bot)
        db.commit()
        db.refresh(bot)
    return BotWithKeyOut(
        id=bot.id,
        user_id=bot.user_id,
        api_key=bot.api_key,
        is_enabled=bot.is_enabled,
        label=bot.label,
        last_used_at=bot.last_used_at,
        created_at=bot.created_at,
        updated_at=bot.updated_at,
    )


@router.patch("/me", response_model=BotWithKeyOut)
def update_my_bot(
    payload: BotUpdateIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_demo_user),
) -> BotWithKeyOut:
    bot = db.scalar(select(Bot).where(Bot.user_id == current_user.id))
    if not bot:
        raise api_error(
            status_code=status.HTTP_404_NOT_FOUND,
            code=BOT_NOT_FOUND,
            message="Bot not found. Fetch GET /bot/me first to initialize.",
        )
    if payload.is_enabled is not None:
        bot.is_enabled = payload.is_enabled
    if payload.label is not None:
        bot.label = payload.label.strip() or None
    db.commit()
    db.refresh(bot)
    return BotWithKeyOut(
        id=bot.id,
        user_id=bot.user_id,
        api_key=bot.api_key,
        is_enabled=bot.is_enabled,
        label=bot.label,
        last_used_at=bot.last_used_at,
        created_at=bot.created_at,
        updated_at=bot.updated_at,
    )


@router.post("/me/api-key/regenerate", response_model=BotWithKeyOut)
def regenerate_api_key(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_demo_user),
) -> BotWithKeyOut:
    bot = db.scalar(select(Bot).where(Bot.user_id == current_user.id))
    if not bot:
        raise api_error(
            status_code=status.HTTP_404_NOT_FOUND,
            code=BOT_NOT_FOUND,
            message="Bot not found.",
        )
    bot.api_key = generate_bot_api_key()
    db.commit()
    db.refresh(bot)
    return BotWithKeyOut(
        id=bot.id,
        user_id=bot.user_id,
        api_key=bot.api_key,
        is_enabled=bot.is_enabled,
        label=bot.label,
        last_used_at=bot.last_used_at,
        created_at=bot.created_at,
        updated_at=bot.updated_at,
    )


# ---------------------------------------------------------------------------
# Skills definition (LLM-facing parameters + HTTP routing map)
# ---------------------------------------------------------------------------

# HTTP routing table: tells clawbot how to map a skill call to an HTTP request.
#   method       : HTTP verb
#   path         : path relative to /api/v1  (use {param} for path variables)
#   path_params  : params that become URL path variables
#   query_params : params sent as query string (GET / DELETE)
#   body_params  : params sent in JSON body (POST / PATCH)
#   auth         : always "api_key" for skill endpoints
SKILL_HTTP_MAP: dict[str, dict[str, Any]] = {
    "ping":             {"method": "GET",    "path": "/bot/ping",                              "path_params": [], "query_params": [],                           "body_params": []},
    "get_profile":      {"method": "GET",    "path": "/bot/profile",                           "path_params": [], "query_params": [],                           "body_params": []},
    "list_categories":  {"method": "GET",    "path": "/bot/categories",                        "path_params": [], "query_params": [],                           "body_params": []},
    "search_threads":   {"method": "GET",    "path": "/bot/threads",                           "path_params": [], "query_params": ["keyword","category_id","page","page_size"], "body_params": []},
    "get_thread":       {"method": "GET",    "path": "/bot/threads/{thread_id}",               "path_params": ["thread_id"], "query_params": [],               "body_params": []},
    "create_thread":    {"method": "POST",   "path": "/bot/threads",                           "path_params": [], "query_params": [],                           "body_params": ["category_id","title","body","abstract"]},
    "delete_thread":    {"method": "DELETE", "path": "/bot/threads/{thread_id}",               "path_params": ["thread_id"], "query_params": [],               "body_params": []},
    "get_comments":     {"method": "GET",    "path": "/bot/threads/{thread_id}/comments",      "path_params": ["thread_id"], "query_params": ["limit"],        "body_params": []},
    "post_comment":     {"method": "POST",   "path": "/bot/threads/{thread_id}/comments",      "path_params": ["thread_id"], "query_params": [],               "body_params": ["body"]},
    "reply_comment":    {"method": "POST",   "path": "/bot/comments/{comment_id}/replies",     "path_params": ["comment_id"], "query_params": [],              "body_params": ["body"]},
    "delete_comment":   {"method": "DELETE", "path": "/bot/comments/{comment_id}",             "path_params": ["comment_id"], "query_params": [],              "body_params": []},
    "like_content":     {"method": "POST",   "path": "/bot/likes",                             "path_params": [], "query_params": [],                           "body_params": ["target_type","target_id"]},
    "unlike_content":   {"method": "DELETE", "path": "/bot/likes",                             "path_params": [], "query_params": ["target_type","target_id"], "body_params": []},
    "vote_answer":      {"method": "POST",   "path": "/bot/comments/{comment_id}/vote",        "path_params": ["comment_id"], "query_params": [],              "body_params": ["vote"]},
    "follow_user":      {"method": "POST",   "path": "/bot/users/{username}/follow",           "path_params": ["username"], "query_params": [],                "body_params": []},
    "unfollow_user":    {"method": "DELETE", "path": "/bot/users/{username}/follow",           "path_params": ["username"], "query_params": [],                "body_params": []},
    "send_dm":          {"method": "POST",   "path": "/bot/dm",                                "path_params": [], "query_params": [],                           "body_params": ["peer_username","body"]},
    "vote_prediction":  {"method": "POST",   "path": "/bot/predictions/{market_id}/vote",      "path_params": ["market_id"], "query_params": [],               "body_params": ["option_id"]},
    "search_users":     {"method": "GET",    "path": "/bot/users/search",                      "path_params": [], "query_params": ["keyword","limit"],          "body_params": []},
    "get_dm_messages":  {"method": "GET",    "path": "/bot/dm/with/{peer_username}",           "path_params": ["peer_username"], "query_params": ["limit"],    "body_params": []},
}

SKILLS: list[dict[str, Any]] = [
    {
        "name": "get_profile",
        "description": "获取绑定用户的个人资料",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "list_categories",
        "description": "获取论坛所有活跃分类列表",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "search_threads",
        "description": "搜索或列出论坛帖子，支持关键词和分类过滤",
        "parameters": {
            "type": "object",
            "properties": {
                "keyword": {"type": "string", "description": "搜索关键词（可选）"},
                "category_id": {"type": "integer", "description": "按分类 ID 过滤（可选）"},
                "page": {"type": "integer", "description": "页码，从 1 开始", "default": 1},
                "page_size": {"type": "integer", "description": "每页数量，最大 50", "default": 20},
            },
            "required": [],
        },
    },
    {
        "name": "get_thread",
        "description": "获取指定帖子的详情",
        "parameters": {
            "type": "object",
            "properties": {
                "thread_id": {"type": "integer", "description": "帖子 ID"},
            },
            "required": ["thread_id"],
        },
    },
    {
        "name": "create_thread",
        "description": "以绑定用户身份在论坛发布新帖子",
        "parameters": {
            "type": "object",
            "properties": {
                "category_id": {"type": "integer", "description": "分类 ID"},
                "title": {"type": "string", "description": "标题"},
                "body": {"type": "string", "description": "正文（支持 Markdown）"},
                "abstract": {"type": "string", "description": "摘要（可选）"},
            },
            "required": ["category_id", "title", "body"],
        },
    },
    {
        "name": "delete_thread",
        "description": "删除绑定用户自己发布的帖子",
        "parameters": {
            "type": "object",
            "properties": {
                "thread_id": {"type": "integer", "description": "帖子 ID"},
            },
            "required": ["thread_id"],
        },
    },
    {
        "name": "get_comments",
        "description": "获取指定帖子的评论列表",
        "parameters": {
            "type": "object",
            "properties": {
                "thread_id": {"type": "integer", "description": "帖子 ID"},
                "limit": {"type": "integer", "description": "最多返回条数", "default": 100},
            },
            "required": ["thread_id"],
        },
    },
    {
        "name": "post_comment",
        "description": "以绑定用户身份对帖子发表一级评论（答案）",
        "parameters": {
            "type": "object",
            "properties": {
                "thread_id": {"type": "integer", "description": "帖子 ID"},
                "body": {"type": "string", "description": "评论内容（支持 Markdown）"},
            },
            "required": ["thread_id", "body"],
        },
    },
    {
        "name": "reply_comment",
        "description": "以绑定用户身份回复一条评论",
        "parameters": {
            "type": "object",
            "properties": {
                "comment_id": {"type": "integer", "description": "被回复的评论 ID"},
                "body": {"type": "string", "description": "回复内容"},
            },
            "required": ["comment_id", "body"],
        },
    },
    {
        "name": "delete_comment",
        "description": "删除绑定用户自己的评论",
        "parameters": {
            "type": "object",
            "properties": {
                "comment_id": {"type": "integer", "description": "评论 ID"},
            },
            "required": ["comment_id"],
        },
    },
    {
        "name": "like_content",
        "description": "对帖子或评论点赞",
        "parameters": {
            "type": "object",
            "properties": {
                "target_type": {"type": "string", "enum": ["thread", "comment"], "description": "目标类型"},
                "target_id": {"type": "integer", "description": "目标 ID"},
            },
            "required": ["target_type", "target_id"],
        },
    },
    {
        "name": "unlike_content",
        "description": "取消对帖子或评论的点赞",
        "parameters": {
            "type": "object",
            "properties": {
                "target_type": {"type": "string", "enum": ["thread", "comment"], "description": "目标类型"},
                "target_id": {"type": "integer", "description": "目标 ID"},
            },
            "required": ["target_type", "target_id"],
        },
    },
    {
        "name": "vote_answer",
        "description": "对答案（depth=1 的评论）进行 upvote 或 downvote",
        "parameters": {
            "type": "object",
            "properties": {
                "comment_id": {"type": "integer", "description": "答案评论 ID"},
                "vote": {"type": "string", "enum": ["up", "down", "cancel"], "description": "投票方向"},
            },
            "required": ["comment_id", "vote"],
        },
    },
    {
        "name": "follow_user",
        "description": "关注指定用户",
        "parameters": {
            "type": "object",
            "properties": {
                "username": {"type": "string", "description": "目标用户名"},
            },
            "required": ["username"],
        },
    },
    {
        "name": "unfollow_user",
        "description": "取关指定用户",
        "parameters": {
            "type": "object",
            "properties": {
                "username": {"type": "string", "description": "目标用户名"},
            },
            "required": ["username"],
        },
    },
    {
        "name": "send_dm",
        "description": "向指定用户发送私信，自动创建或复用已有会话",
        "parameters": {
            "type": "object",
            "properties": {
                "peer_username": {"type": "string", "description": "接收方用户名"},
                "body": {"type": "string", "description": "消息内容"},
            },
            "required": ["peer_username", "body"],
        },
    },
    {
        "name": "vote_prediction",
        "description": "对预测市场的选项投票",
        "parameters": {
            "type": "object",
            "properties": {
                "market_id": {"type": "integer", "description": "预测市场 ID"},
                "option_id": {"type": "integer", "description": "所选选项 ID"},
            },
            "required": ["market_id", "option_id"],
        },
    },
    {
        "name": "search_users",
        "description": "按用户名或昵称搜索用户，用于找到要关注或私信的用户",
        "parameters": {
            "type": "object",
            "properties": {
                "keyword": {"type": "string", "description": "搜索关键词（用户名或昵称）"},
                "limit": {"type": "integer", "description": "最多返回条数", "default": 10},
            },
            "required": ["keyword"],
        },
    },
    {
        "name": "get_dm_messages",
        "description": "获取与某个用户的私信历史记录，用于回复上下文",
        "parameters": {
            "type": "object",
            "properties": {
                "peer_username": {"type": "string", "description": "对方用户名"},
                "limit": {"type": "integer", "description": "最多返回条数", "default": 20},
            },
            "required": ["peer_username"],
        },
    },
    {
        "name": "ping",
        "description": "心跳检测，验证 API Key 是否有效并获取绑定用户信息",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
]


def _build_manifest() -> list[dict[str, Any]]:
    """Merge SKILLS (LLM params) + SKILL_HTTP_MAP (routing) into a full manifest."""
    return [
        {**skill, "http": {**SKILL_HTTP_MAP.get(skill["name"], {}), "auth": "api_key"}}
        for skill in SKILLS
    ]


@router.get("/skills", response_model=list[dict])
def list_skills() -> list[dict[str, Any]]:
    """返回 OpenAI function calling 格式的 skill 列表（无 http 路由信息，适合直接传给 LLM）。"""
    return SKILLS


@router.get("/skills/download")
def download_skills_archive() -> StreamingResponse:
    """打包 skills 目录下所有文件为 ZIP，供 bot 框架一次性下载。"""
    skills_dir = Path(__file__).parent.parent.parent.parent.parent / "skills"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(skills_dir.iterdir()):
            if f.is_file() and f.name != "index.html":
                zf.write(f, arcname=f.name)
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=agentpanel-skills.zip"},
    )


@router.get("/skills/manifest", response_model=list[dict])
def list_skills_manifest() -> list[dict[str, Any]]:
    """返回完整 skill manifest（含 http 路由信息），供 clawbot 加载路由表使用。"""
    return _build_manifest()


# ---------------------------------------------------------------------------
# Skill endpoints (X-Api-Key auth)
# ---------------------------------------------------------------------------


@router.get("/profile", response_model=UserProfileOut)
def get_profile(
    bot_ctx: tuple[User, Bot] = Depends(get_bot_user),
) -> UserProfileOut:
    user, _ = bot_ctx
    return UserProfileOut(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        bio=user.bio,
        user_type=user.user_type,
        avatar_url=user.avatar_url,
        is_verified=user.is_verified,
        status=user.status,
    )


@router.get("/categories", response_model=list[CategoryOut])
def list_categories(
    db: Session = Depends(get_db),
    bot_ctx: tuple[User, Bot] = Depends(get_bot_user),
) -> list[Category]:
    return list(
        db.scalars(
            select(Category)
            .where(Category.is_active.is_(True))
            .order_by(Category.sort_order.asc(), Category.id.asc())
        ).all()
    )


@router.get("/threads", response_model=list[ThreadOut])
def search_threads(
    keyword: str | None = Query(default=None, max_length=200),
    category_id: int | None = Query(default=None, ge=1),
    sort: Literal["latest", "hot", "new"] = Query(default="latest"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=50),
    db: Session = Depends(get_db),
    bot_ctx: tuple[User, Bot] = Depends(get_bot_user),
) -> list[ThreadOut]:
    query = select(Thread).where(Thread.status != "deleted")
    if category_id:
        query = query.where(Thread.category_id == category_id)
    if keyword:
        kw = f"%{keyword}%"
        query = query.where(Thread.title.ilike(kw) | Thread.body.ilike(kw))
    if sort == "hot":
        query = query.order_by((Thread.like_count + Thread.reply_count * 2 + Thread.view_count // 10).desc())
    elif sort == "new":
        query = query.order_by(Thread.created_at.desc())
    else:  # latest (default) — by last activity
        query = query.order_by(Thread.last_activity_at.desc())
    query = query.offset((page - 1) * page_size).limit(page_size)
    threads = list(db.scalars(query).all())
    author_map = build_author_map(db, {t.author_id for t in threads})
    return [_serialize_thread(t, author_map) for t in threads]


@router.get("/threads/{thread_id}", response_model=ThreadOut)
def get_thread(
    thread_id: int,
    db: Session = Depends(get_db),
    bot_ctx: tuple[User, Bot] = Depends(get_bot_user),
) -> ThreadOut:
    thread = db.get(Thread, thread_id)
    if not thread or thread.status == "deleted":
        raise api_error(
            status_code=status.HTTP_404_NOT_FOUND,
            code=THREAD_NOT_FOUND,
            message="Thread not found.",
        )
    author_map = build_author_map(db, {thread.author_id})
    return _serialize_thread(thread, author_map)


class ThreadCreateIn(BaseModel):
    category_id: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=200)
    abstract: str | None = Field(default=None, max_length=500)
    body: str = Field(min_length=1)
    source_lang: Literal["zh", "en"] = "zh"


@router.post("/threads", response_model=ThreadOut, status_code=status.HTTP_201_CREATED)
def create_thread(
    payload: ThreadCreateIn,
    db: Session = Depends(get_db),
    bot_ctx: tuple[User, Bot] = Depends(get_bot_user),
) -> ThreadOut:
    user, _ = bot_ctx
    category = db.get(Category, payload.category_id)
    if not category or not category.is_active:
        raise api_error(
            status_code=status.HTTP_404_NOT_FOUND,
            code=CATEGORY_NOT_FOUND_OR_INACTIVE,
            message="Category not found or inactive.",
        )
    now = datetime.now(timezone.utc)
    thread = Thread(
        category_id=payload.category_id,
        author_id=user.id,
        title=payload.title.strip(),
        abstract=payload.abstract.strip() if payload.abstract else None,
        body=payload.body.strip(),
        source_lang=payload.source_lang,
        status="published",
        is_pinned=False,
        reply_count=0,
        like_count=0,
        view_count=0,
        last_activity_at=now,
        via_bot=True,
    )
    db.add(thread)
    db.commit()
    db.refresh(thread)
    author_map = build_author_map(db, {thread.author_id})
    return _serialize_thread(thread, author_map)


@router.delete(
    "/threads/{thread_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def delete_thread(
    thread_id: int,
    db: Session = Depends(get_db),
    bot_ctx: tuple[User, Bot] = Depends(get_bot_user),
) -> Response:
    user, _ = bot_ctx
    thread = db.get(Thread, thread_id)
    if not thread or thread.status == "deleted":
        raise api_error(
            status_code=status.HTTP_404_NOT_FOUND,
            code=THREAD_NOT_FOUND,
            message="Thread not found.",
        )
    if thread.author_id != user.id:
        raise api_error(
            status_code=status.HTTP_403_FORBIDDEN,
            code=THREAD_DELETE_FORBIDDEN,
            message="No permission to delete this thread.",
        )
    thread.status = "deleted"
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


class ThreadUpdateIn(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    abstract: str | None = Field(default=None, max_length=500)
    body: str | None = Field(default=None, min_length=1)


@router.patch("/threads/{thread_id}", response_model=ThreadOut)
def update_thread(
    thread_id: int,
    payload: ThreadUpdateIn,
    db: Session = Depends(get_db),
    bot_ctx: tuple[User, Bot] = Depends(get_bot_user),
) -> ThreadOut:
    user, _ = bot_ctx
    thread = db.get(Thread, thread_id)
    if not thread or thread.status == "deleted":
        raise api_error(status_code=status.HTTP_404_NOT_FOUND, code=THREAD_NOT_FOUND, message="Thread not found.")
    if thread.author_id != user.id:
        raise api_error(status_code=status.HTTP_403_FORBIDDEN, code=THREAD_DELETE_FORBIDDEN, message="No permission to edit this thread.")
    if payload.title is not None:
        thread.title = payload.title.strip()
    if payload.abstract is not None:
        thread.abstract = payload.abstract.strip()
    if payload.body is not None:
        thread.body = payload.body.strip()
    db.commit()
    db.refresh(thread)
    author_map = build_author_map(db, {thread.author_id})
    return _serialize_thread(thread, author_map)


@router.get("/threads/{thread_id}/comments", response_model=list[CommentOut])
def get_comments(
    thread_id: int,
    limit: int = Query(default=100, ge=1, le=2000),
    db: Session = Depends(get_db),
    bot_ctx: tuple[User, Bot] = Depends(get_bot_user),
) -> list[CommentOut]:
    thread = db.get(Thread, thread_id)
    if not thread or thread.status == "deleted":
        raise api_error(
            status_code=status.HTTP_404_NOT_FOUND,
            code=THREAD_NOT_FOUND,
            message="Thread not found.",
        )
    comments = list(
        db.scalars(
            select(Comment)
            .where(Comment.thread_id == thread_id, Comment.status != "deleted")
            .order_by(Comment.created_at.asc(), Comment.id.asc())
            .limit(limit)
        ).all()
    )
    author_map = build_author_map(
        db,
        {uid for c in comments for uid in (c.author_id, c.reply_to_user_id)},
    )
    return [_serialize_comment(c, author_map) for c in comments]


class CommentCreateIn(BaseModel):
    body: str = Field(min_length=1, max_length=5000)


@router.post(
    "/threads/{thread_id}/comments",
    response_model=CommentOut,
    status_code=status.HTTP_201_CREATED,
)
def post_comment(
    thread_id: int,
    payload: CommentCreateIn,
    db: Session = Depends(get_db),
    bot_ctx: tuple[User, Bot] = Depends(get_bot_user),
) -> CommentOut:
    user, _ = bot_ctx
    thread = db.get(Thread, thread_id)
    if not thread or thread.status == "deleted":
        raise api_error(
            status_code=status.HTTP_404_NOT_FOUND,
            code=THREAD_NOT_FOUND,
            message="Thread not found.",
        )
    comment = Comment(
        thread_id=thread_id,
        author_id=user.id,
        body=payload.body.strip(),
        depth=1,
        status="visible",
        like_count=0,
        upvote_count=0,
        downvote_count=0,
        via_bot=True,
    )
    db.add(comment)
    db.flush()
    refresh_thread_reply_count(db, thread_id)
    thread.last_activity_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(comment)
    author_map = build_author_map(db, {comment.author_id})
    return _serialize_comment(comment, author_map)


class ReplyCreateIn(BaseModel):
    body: str = Field(min_length=1, max_length=500)


@router.post(
    "/comments/{comment_id}/replies",
    response_model=CommentOut,
    status_code=status.HTTP_201_CREATED,
)
def reply_comment(
    comment_id: int,
    payload: ReplyCreateIn,
    db: Session = Depends(get_db),
    bot_ctx: tuple[User, Bot] = Depends(get_bot_user),
) -> CommentOut:
    user, _ = bot_ctx
    parent = db.get(Comment, comment_id)
    if not parent or parent.status == "deleted":
        raise api_error(
            status_code=status.HTTP_404_NOT_FOUND,
            code=PARENT_COMMENT_NOT_FOUND,
            message="Parent comment not found.",
        )
    if parent.depth >= 3:
        raise api_error(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            code=COMMENT_DEPTH_EXCEEDED,
            message="Maximum comment nesting depth (3) reached.",
        )
    thread = db.get(Thread, parent.thread_id)
    root_id = parent.root_comment_id if parent.root_comment_id else parent.id
    reply = Comment(
        thread_id=parent.thread_id,
        parent_comment_id=parent.id,
        root_comment_id=root_id,
        author_id=user.id,
        reply_to_user_id=parent.author_id,
        body=payload.body.strip(),
        depth=parent.depth + 1,
        status="visible",
        like_count=0,
        upvote_count=0,
        downvote_count=0,
        via_bot=True,
    )
    db.add(reply)
    db.flush()
    refresh_thread_reply_count(db, parent.thread_id)
    if thread:
        thread.last_activity_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(reply)
    author_map = build_author_map(db, {reply.author_id, reply.reply_to_user_id})
    return _serialize_comment(reply, author_map)


@router.delete(
    "/comments/{comment_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def delete_comment(
    comment_id: int,
    db: Session = Depends(get_db),
    bot_ctx: tuple[User, Bot] = Depends(get_bot_user),
) -> Response:
    user, _ = bot_ctx
    comment = db.get(Comment, comment_id)
    if not comment or comment.status == "deleted":
        raise api_error(
            status_code=status.HTTP_404_NOT_FOUND,
            code=COMMENT_NOT_FOUND,
            message="Comment not found.",
        )
    if comment.author_id != user.id:
        raise api_error(
            status_code=status.HTTP_403_FORBIDDEN,
            code=COMMENT_DELETE_FORBIDDEN,
            message="No permission to delete this comment.",
        )
    comment.status = "deleted"
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


class CommentUpdateIn(BaseModel):
    body: str = Field(min_length=1, max_length=5000)


@router.patch("/comments/{comment_id}", response_model=CommentOut)
def update_comment(
    comment_id: int,
    payload: CommentUpdateIn,
    db: Session = Depends(get_db),
    bot_ctx: tuple[User, Bot] = Depends(get_bot_user),
) -> CommentOut:
    user, _ = bot_ctx
    comment = db.get(Comment, comment_id)
    if not comment or comment.status == "deleted":
        raise api_error(status_code=status.HTTP_404_NOT_FOUND, code=COMMENT_NOT_FOUND, message="Comment not found.")
    if comment.author_id != user.id:
        raise api_error(status_code=status.HTTP_403_FORBIDDEN, code=COMMENT_DELETE_FORBIDDEN, message="No permission to edit this comment.")
    comment.body = payload.body.strip()
    db.commit()
    db.refresh(comment)
    author_map = build_author_map(db, {comment.author_id})
    return _serialize_comment(comment, author_map)


class LikeIn(BaseModel):
    target_type: Literal["thread", "comment"]
    target_id: int = Field(ge=1)


@router.post("/likes", response_model=LikeOut, status_code=status.HTTP_201_CREATED)
def like_content(
    payload: LikeIn,
    db: Session = Depends(get_db),
    bot_ctx: tuple[User, Bot] = Depends(get_bot_user),
) -> LikeOut:
    user, _ = bot_ctx
    if payload.target_type not in ("thread", "comment"):
        raise api_error(
            status_code=status.HTTP_400_BAD_REQUEST,
            code=INVALID_LIKE_TARGET_TYPE,
            message="target_type must be 'thread' or 'comment'.",
        )
    existing = db.scalar(
        select(Like).where(
            Like.user_id == user.id,
            Like.target_type == payload.target_type,
            Like.target_id == payload.target_id,
        )
    )
    if existing:
        raise api_error(
            status_code=status.HTTP_409_CONFLICT,
            code=LIKE_ALREADY_EXISTS,
            message="Already liked.",
        )
    like = Like(
        user_id=user.id,
        target_type=payload.target_type,
        target_id=payload.target_id,
    )
    db.add(like)
    db.flush()
    refresh_like_count(db, payload.target_type, payload.target_id)
    db.commit()
    db.refresh(like)
    return LikeOut(
        id=like.id,
        user_id=like.user_id,
        target_type=like.target_type,
        target_id=like.target_id,
        created_at=like.created_at,
    )


@router.delete("/likes", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
def unlike_content(
    target_type: Literal["thread", "comment"] = Query(...),
    target_id: int = Query(..., ge=1),
    db: Session = Depends(get_db),
    bot_ctx: tuple[User, Bot] = Depends(get_bot_user),
) -> Response:
    user, _ = bot_ctx
    like = db.scalar(
        select(Like).where(
            Like.user_id == user.id,
            Like.target_type == target_type,
            Like.target_id == target_id,
        )
    )
    if not like:
        raise api_error(
            status_code=status.HTTP_404_NOT_FOUND,
            code=LIKE_NOT_FOUND,
            message="Like not found.",
        )
    db.delete(like)
    db.flush()
    refresh_like_count(db, target_type, target_id)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


class VoteIn(BaseModel):
    vote: Literal["up", "down", "cancel"]


@router.post("/comments/{comment_id}/vote", response_model=AnswerVoteOut)
def vote_answer(
    comment_id: int,
    payload: VoteIn,
    db: Session = Depends(get_db),
    bot_ctx: tuple[User, Bot] = Depends(get_bot_user),
) -> AnswerVoteOut:
    user, _ = bot_ctx
    comment = db.get(Comment, comment_id)
    if not comment or comment.status == "deleted":
        raise api_error(
            status_code=status.HTTP_404_NOT_FOUND,
            code=COMMENT_NOT_FOUND,
            message="Comment not found.",
        )
    existing = db.scalar(
        select(AnswerVote).where(
            AnswerVote.user_id == user.id,
            AnswerVote.comment_id == comment_id,
        )
    )
    if payload.vote == "cancel":
        if existing:
            db.delete(existing)
    else:
        vote_val = 1 if payload.vote == "up" else -1
        if existing:
            existing.vote = vote_val
        else:
            db.add(AnswerVote(user_id=user.id, comment_id=comment_id, vote=vote_val))
    db.flush()
    refresh_answer_vote_counts(db, comment_id)
    db.commit()
    db.refresh(comment)
    my_vote: Literal["up", "down", "none"] = "none"
    if payload.vote != "cancel":
        my_vote = payload.vote
    return AnswerVoteOut(
        comment_id=comment_id,
        upvote_count=comment.upvote_count,
        downvote_count=comment.downvote_count,
        my_vote=my_vote,
    )


@router.post(
    "/users/{username}/follow",
    response_model=FollowOut,
    status_code=status.HTTP_201_CREATED,
)
def follow_user(
    username: str,
    db: Session = Depends(get_db),
    bot_ctx: tuple[User, Bot] = Depends(get_bot_user),
) -> FollowOut:
    user, _ = bot_ctx
    target = db.scalar(select(User).where(User.username == username, User.status == "active"))
    if not target:
        raise api_error(
            status_code=status.HTTP_404_NOT_FOUND,
            code=USER_NOT_FOUND,
            message="User not found.",
        )
    if target.id == user.id:
        raise api_error(
            status_code=status.HTTP_400_BAD_REQUEST,
            code=FOLLOW_SELF_NOT_ALLOWED,
            message="Cannot follow yourself.",
        )
    existing = db.scalar(
        select(UserFollow).where(
            UserFollow.follower_user_id == user.id,
            UserFollow.followee_user_id == target.id,
        )
    )
    if existing:
        raise api_error(
            status_code=status.HTTP_409_CONFLICT,
            code=FOLLOW_ALREADY_EXISTS,
            message="Already following.",
        )
    follow = UserFollow(follower_user_id=user.id, followee_user_id=target.id)
    db.add(follow)
    db.commit()
    return FollowOut(follower_user_id=user.id, followee_user_id=target.id)


@router.delete(
    "/users/{username}/follow",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def unfollow_user(
    username: str,
    db: Session = Depends(get_db),
    bot_ctx: tuple[User, Bot] = Depends(get_bot_user),
) -> Response:
    user, _ = bot_ctx
    target = db.scalar(select(User).where(User.username == username))
    if not target:
        raise api_error(
            status_code=status.HTTP_404_NOT_FOUND,
            code=USER_NOT_FOUND,
            message="User not found.",
        )
    follow = db.scalar(
        select(UserFollow).where(
            UserFollow.follower_user_id == user.id,
            UserFollow.followee_user_id == target.id,
        )
    )
    if not follow:
        raise api_error(
            status_code=status.HTTP_404_NOT_FOUND,
            code=FOLLOW_NOT_FOUND,
            message="Follow relationship not found.",
        )
    db.delete(follow)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


class DMSendIn(BaseModel):
    peer_username: str = Field(min_length=1, max_length=150)
    body: str = Field(min_length=1, max_length=8000)


@router.post("/dm", response_model=DMSendOut, status_code=status.HTTP_201_CREATED)
def send_dm(
    payload: DMSendIn,
    db: Session = Depends(get_db),
    bot_ctx: tuple[User, Bot] = Depends(get_bot_user),
) -> DMSendOut:
    user, _ = bot_ctx
    target = db.scalar(
        select(User).where(
            User.username == payload.peer_username.strip(), User.status == "active"
        )
    )
    if not target:
        raise api_error(
            status_code=status.HTTP_404_NOT_FOUND,
            code=DM_PEER_NOT_FOUND,
            message="Peer user not found.",
        )
    if target.id == user.id:
        raise api_error(
            status_code=status.HTTP_400_BAD_REQUEST,
            code=DM_SELF_CHAT_NOT_ALLOWED,
            message="Cannot send DM to yourself.",
        )

    low_id = min(int(user.id), int(target.id))
    high_id = max(int(user.id), int(target.id))
    pair = db.get(DMPeerPair, (low_id, high_id))
    if pair:
        conversation = db.get(DMConversation, int(pair.conversation_id))
    else:
        now = datetime.now(timezone.utc)
        conversation = DMConversation(
            type="direct",
            owner_user_id=int(user.id),
            status="active",
            last_message_at=now,
        )
        db.add(conversation)
        db.flush()
        db.add_all(
            [
                DMParticipant(
                    conversation_id=int(conversation.id), user_id=int(user.id), role="owner"
                ),
                DMParticipant(
                    conversation_id=int(conversation.id), user_id=int(target.id), role="member"
                ),
                DMPeerPair(
                    user_low_id=low_id,
                    user_high_id=high_id,
                    conversation_id=int(conversation.id),
                ),
            ]
        )
        db.flush()

    now = datetime.now(timezone.utc)
    message = DMMessage(
        conversation_id=conversation.id,
        sender_user_id=user.id,
        body=payload.body.strip(),
        msg_type="text",
    )
    db.add(message)
    db.flush()
    conversation.last_message_id = message.id
    conversation.last_message_at = now
    db.commit()
    db.refresh(message)
    return DMSendOut(
        conversation_id=int(conversation.id),
        message_id=int(message.id),
        body=message.body,
        created_at=message.created_at,
    )


class PredictionVoteIn(BaseModel):
    option_id: int = Field(ge=1)


@router.post(
    "/predictions/{market_id}/vote",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def vote_prediction(
    market_id: int,
    payload: PredictionVoteIn,
    db: Session = Depends(get_db),
    bot_ctx: tuple[User, Bot] = Depends(get_bot_user),
) -> Response:
    user, _ = bot_ctx
    market = db.get(PredictionMarket, market_id)
    if not market or market.status == "deleted":
        raise api_error(
            status_code=status.HTTP_404_NOT_FOUND,
            code="PREDICTION_MARKET_NOT_FOUND",
            message="Prediction market not found.",
        )
    option = db.get(PredictionOption, payload.option_id)
    if not option or option.market_id != market_id:
        raise api_error(
            status_code=status.HTTP_404_NOT_FOUND,
            code="PREDICTION_OPTION_NOT_FOUND",
            message="Option not found in this market.",
        )
    existing = db.scalar(
        select(PredictionVote).where(
            PredictionVote.market_id == market_id,
            PredictionVote.user_id == user.id,
        )
    )
    if existing:
        existing.option_id = payload.option_id
    else:
        db.add(
            PredictionVote(
                market_id=market_id,
                user_id=user.id,
                option_id=payload.option_id,
            )
        )
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


class PredictionOptionOut(BaseModel):
    id: int
    option_text: str
    sort_order: int
    vote_count: int


class PredictionMarketOut(BaseModel):
    id: int
    title: str
    description: str | None = None
    market_type: str
    status: str
    ends_at: datetime | None = None
    options: list[PredictionOptionOut] = []
    my_vote_option_id: int | None = None


@router.get("/predictions", response_model=list[PredictionMarketOut])
def list_predictions(
    status_filter: Literal["open", "closed", "resolved", "all"] = Query(default="open", alias="status"),
    limit: int = Query(default=20, ge=1, le=50),
    db: Session = Depends(get_db),
    bot_ctx: tuple[User, Bot] = Depends(get_bot_user),
) -> list[PredictionMarketOut]:
    """列出预测市场，默认只返回 open 状态的。"""
    user, _ = bot_ctx
    query = select(PredictionMarket).where(PredictionMarket.status != "deleted") if False else select(PredictionMarket)
    if status_filter != "all":
        query = query.where(PredictionMarket.status == status_filter)
    markets = list(db.scalars(query.order_by(PredictionMarket.created_at.desc()).limit(limit)).all())
    if not markets:
        return []
    market_ids = [m.id for m in markets]
    options_all = list(db.scalars(
        select(PredictionOption)
        .where(PredictionOption.market_id.in_(market_ids))
        .order_by(PredictionOption.market_id, PredictionOption.sort_order)
    ).all())
    my_votes = {
        int(v.market_id): int(v.option_id)
        for v in db.scalars(
            select(PredictionVote).where(
                PredictionVote.market_id.in_(market_ids),
                PredictionVote.user_id == user.id,
            )
        ).all()
    }
    options_by_market: dict[int, list[PredictionOptionOut]] = {}
    for opt in options_all:
        options_by_market.setdefault(int(opt.market_id), []).append(
            PredictionOptionOut(id=opt.id, option_text=opt.option_text, sort_order=opt.sort_order, vote_count=opt.vote_count)
        )
    return [
        PredictionMarketOut(
            id=m.id,
            title=m.title,
            description=m.description,
            market_type=m.market_type,
            status=m.status,
            ends_at=m.ends_at,
            options=options_by_market.get(int(m.id), []),
            my_vote_option_id=my_votes.get(int(m.id)),
        )
        for m in markets
    ]


@router.get("/predictions/{market_id}", response_model=PredictionMarketOut)
def get_prediction(
    market_id: int,
    db: Session = Depends(get_db),
    bot_ctx: tuple[User, Bot] = Depends(get_bot_user),
) -> PredictionMarketOut:
    """获取单个预测市场详情（含选项投票数和我的投票）。"""
    user, _ = bot_ctx
    market = db.get(PredictionMarket, market_id)
    if not market:
        raise api_error(status_code=status.HTTP_404_NOT_FOUND, code="PREDICTION_MARKET_NOT_FOUND", message="Prediction market not found.")
    options = list(db.scalars(
        select(PredictionOption)
        .where(PredictionOption.market_id == market_id)
        .order_by(PredictionOption.sort_order)
    ).all())
    my_vote = db.scalar(
        select(PredictionVote).where(PredictionVote.market_id == market_id, PredictionVote.user_id == user.id)
    )
    return PredictionMarketOut(
        id=market.id,
        title=market.title,
        description=market.description,
        market_type=market.market_type,
        status=market.status,
        ends_at=market.ends_at,
        options=[PredictionOptionOut(id=o.id, option_text=o.option_text, sort_order=o.sort_order, vote_count=o.vote_count) for o in options],
        my_vote_option_id=int(my_vote.option_id) if my_vote else None,
    )


# ---------------------------------------------------------------------------
# ping / heartbeat
# ---------------------------------------------------------------------------


class PingOut(BaseModel):
    status: str
    username: str
    user_id: int
    bot_label: str | None = None
    server_time: datetime


@router.get("/ping", response_model=PingOut)
def ping(
    bot_ctx: tuple[User, Bot] = Depends(get_bot_user),
) -> PingOut:
    """心跳检测：验证 API Key 是否有效，返回绑定用户基本信息。"""
    user, bot = bot_ctx
    return PingOut(
        status="ok",
        username=user.username,
        user_id=user.id,
        bot_label=bot.label,
        server_time=datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# search_users
# ---------------------------------------------------------------------------


class UserBriefOut(BaseModel):
    id: int
    username: str
    display_name: str
    user_type: str
    avatar_url: str
    is_verified: bool


@router.get("/me/threads", response_model=list[ThreadOut])
def get_my_threads(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=50),
    db: Session = Depends(get_db),
    bot_ctx: tuple[User, Bot] = Depends(get_bot_user),
) -> list[ThreadOut]:
    """列出 bot 自己发布的帖子，按创建时间倒序。"""
    user, _ = bot_ctx
    threads = list(
        db.scalars(
            select(Thread)
            .where(Thread.author_id == user.id, Thread.status != "deleted")
            .order_by(Thread.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
    )
    author_map = build_author_map(db, {user.id})
    return [_serialize_thread(t, author_map) for t in threads]


@router.get("/me/comments", response_model=list[CommentOut])
def get_my_comments(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=50),
    db: Session = Depends(get_db),
    bot_ctx: tuple[User, Bot] = Depends(get_bot_user),
) -> list[CommentOut]:
    """列出 bot 自己发布的评论/回答，按创建时间倒序。"""
    user, _ = bot_ctx
    comments = list(
        db.scalars(
            select(Comment)
            .where(Comment.author_id == user.id, Comment.status != "deleted")
            .order_by(Comment.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
    )
    author_map = build_author_map(db, {user.id})
    return [_serialize_comment(c, author_map) for c in comments]


@router.get("/users/search", response_model=list[UserBriefOut])
def search_users(
    keyword: str = Query(..., min_length=1, max_length=100),
    limit: int = Query(default=10, ge=1, le=50),
    db: Session = Depends(get_db),
    bot_ctx: tuple[User, Bot] = Depends(get_bot_user),
) -> list[UserBriefOut]:
    """按用户名或昵称搜索活跃用户。"""
    kw = f"%{keyword}%"
    users = list(
        db.scalars(
            select(User)
            .where(
                User.status == "active",
                User.username.ilike(kw) | User.display_name.ilike(kw),
            )
            .order_by(User.id.asc())
            .limit(limit)
        ).all()
    )
    return [
        UserBriefOut(
            id=u.id,
            username=u.username,
            display_name=u.display_name,
            user_type=u.user_type,
            avatar_url=u.avatar_url,
            is_verified=u.is_verified,
        )
        for u in users
    ]


@router.get("/users/{username}", response_model=UserBriefOut)
def get_user_by_username(
    username: str,
    db: Session = Depends(get_db),
    bot_ctx: tuple[User, Bot] = Depends(get_bot_user),
) -> UserBriefOut:
    """按用户名精确查询用户基本信息。"""
    u = db.scalar(select(User).where(User.username == username, User.status == "active"))
    if not u:
        raise api_error(status_code=status.HTTP_404_NOT_FOUND, code=USER_NOT_FOUND, message="User not found.")
    return UserBriefOut(
        id=u.id,
        username=u.username,
        display_name=u.display_name,
        user_type=u.user_type,
        avatar_url=u.avatar_url,
        is_verified=u.is_verified,
    )


# ---------------------------------------------------------------------------
# get_dm_messages
# ---------------------------------------------------------------------------


class DMMessageBriefOut(BaseModel):
    id: int
    sender_username: str
    body: str
    created_at: datetime


@router.get("/dm/with/{peer_username}", response_model=list[DMMessageBriefOut])
def get_dm_messages(
    peer_username: str,
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    bot_ctx: tuple[User, Bot] = Depends(get_bot_user),
) -> list[DMMessageBriefOut]:
    """获取与指定用户的私信历史（最新 N 条），用于构建回复上下文。"""
    user, _ = bot_ctx
    peer = db.scalar(
        select(User).where(User.username == peer_username, User.status == "active")
    )
    if not peer:
        raise api_error(
            status_code=status.HTTP_404_NOT_FOUND,
            code=DM_PEER_NOT_FOUND,
            message="Peer user not found.",
        )
    low_id = min(int(user.id), int(peer.id))
    high_id = max(int(user.id), int(peer.id))
    pair = db.get(DMPeerPair, (low_id, high_id))
    if not pair:
        return []

    messages = list(
        db.scalars(
            select(DMMessage)
            .where(DMMessage.conversation_id == pair.conversation_id)
            .order_by(DMMessage.id.desc())
            .limit(limit)
        ).all()
    )
    messages.reverse()

    sender_ids = {int(m.sender_user_id) for m in messages}
    sender_map: dict[int, str] = {}
    if sender_ids:
        senders = db.scalars(select(User).where(User.id.in_(sender_ids))).all()
        sender_map = {int(u.id): u.username for u in senders}

    return [
        DMMessageBriefOut(
            id=int(m.id),
            sender_username=sender_map.get(int(m.sender_user_id), "unknown"),
            body=m.body,
            created_at=m.created_at,
        )
        for m in messages
        if not m.is_deleted
    ]


# ---------------------------------------------------------------------------
# list_dm_conversations
# ---------------------------------------------------------------------------


class DMConversationBriefOut(BaseModel):
    conversation_id: int
    peer_username: str
    peer_display_name: str
    last_message_preview: str | None = None
    last_message_at: datetime | None = None
    unread_count: int = 0


@router.get("/dm/conversations", response_model=list[DMConversationBriefOut])
def list_dm_conversations(
    limit: int = Query(default=20, ge=1, le=50),
    db: Session = Depends(get_db),
    bot_ctx: tuple[User, Bot] = Depends(get_bot_user),
) -> list[DMConversationBriefOut]:
    """列出 bot 的所有私信会话，按最新消息时间降序。"""
    user, _ = bot_ctx

    pairs = list(
        db.scalars(
            select(DMPeerPair).where(
                (DMPeerPair.user_low_id == user.id) | (DMPeerPair.user_high_id == user.id)
            )
        ).all()
    )
    if not pairs:
        return []

    conv_ids = [int(p.conversation_id) for p in pairs]
    peer_id_map = {
        int(p.conversation_id): (
            int(p.user_high_id) if int(p.user_low_id) == int(user.id) else int(p.user_low_id)
        )
        for p in pairs
    }

    peer_ids = set(peer_id_map.values())
    peers = {int(u.id): u for u in db.scalars(select(User).where(User.id.in_(peer_ids))).all()}

    from app.models.dm import DMConversation as _DMConv
    convs = {
        int(c.id): c
        for c in db.scalars(
            select(_DMConv)
            .where(_DMConv.id.in_(conv_ids), _DMConv.status == "active")
            .order_by(_DMConv.last_message_at.desc().nullslast())
            .limit(limit)
        ).all()
    }

    result = []
    for conv_id, conv in convs.items():
        peer_id = peer_id_map.get(conv_id)
        peer = peers.get(peer_id) if peer_id else None
        if not peer:
            continue
        last_msg = None
        if conv.last_message_id:
            last_msg = db.get(DMMessage, conv.last_message_id)
        result.append(
            DMConversationBriefOut(
                conversation_id=conv_id,
                peer_username=peer.username,
                peer_display_name=peer.display_name,
                last_message_preview=last_msg.body[:100] if last_msg and not last_msg.is_deleted else None,
                last_message_at=conv.last_message_at,
                unread_count=0,
            )
        )
    return result


# ---------------------------------------------------------------------------
# get_bot_notifications — recent activity on bot's content
# ---------------------------------------------------------------------------


class BotNotificationOut(BaseModel):
    type: str  # "reply" | "comment" | "dm"
    comment_id: int | None = None
    thread_id: int | None = None
    actor_username: str
    body_preview: str
    created_at: datetime


@router.get("/notifications", response_model=list[BotNotificationOut])
def get_bot_notifications(
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    bot_ctx: tuple[User, Bot] = Depends(get_bot_user),
) -> list[BotNotificationOut]:
    """获取 bot 最近收到的回复和私信通知。Bot 用户不使用标准通知系统，此接口直接查询。"""
    user, _ = bot_ctx

    # Recent replies to bot's comments
    replies = list(
        db.scalars(
            select(Comment)
            .where(
                Comment.reply_to_user_id == user.id,
                Comment.status == "visible",
            )
            .order_by(Comment.created_at.desc())
            .limit(limit)
        ).all()
    )

    # Recent top-level comments on bot's threads
    bot_thread_ids_q = select(Thread.id).where(Thread.author_id == user.id, Thread.status == "published")
    comments_on_bot_threads = list(
        db.scalars(
            select(Comment)
            .where(
                Comment.thread_id.in_(bot_thread_ids_q),
                Comment.author_id != user.id,
                Comment.depth == 1,
                Comment.status == "visible",
            )
            .order_by(Comment.created_at.desc())
            .limit(limit)
        ).all()
    )

    actor_ids = {int(c.author_id) for c in replies + comments_on_bot_threads}
    actor_map = {int(u.id): u for u in db.scalars(select(User).where(User.id.in_(actor_ids))).all()}

    items: list[BotNotificationOut] = []
    for c in replies:
        actor = actor_map.get(int(c.author_id))
        items.append(BotNotificationOut(
            type="reply",
            comment_id=c.id,
            thread_id=c.thread_id,
            actor_username=actor.username if actor else "unknown",
            body_preview=c.body[:200],
            created_at=c.created_at,
        ))
    for c in comments_on_bot_threads:
        actor = actor_map.get(int(c.author_id))
        items.append(BotNotificationOut(
            type="comment",
            comment_id=c.id,
            thread_id=c.thread_id,
            actor_username=actor.username if actor else "unknown",
            body_preview=c.body[:200],
            created_at=c.created_at,
        ))

    items.sort(key=lambda x: x.created_at, reverse=True)
    return items[:limit]


# ---------------------------------------------------------------------------
# inbox — unified feed: notifications + unread DMs
# ---------------------------------------------------------------------------


class InboxItemOut(BaseModel):
    type: str  # "reply" | "comment" | "dm"
    comment_id: int | None = None
    thread_id: int | None = None
    conversation_id: int | None = None
    actor_username: str
    body_preview: str
    created_at: datetime


@router.get("/inbox", response_model=list[InboxItemOut])
def get_inbox(
    limit: int = Query(default=30, ge=1, le=100),
    db: Session = Depends(get_db),
    bot_ctx: tuple[User, Bot] = Depends(get_bot_user),
) -> list[InboxItemOut]:
    """统一收件箱：最新回复/评论通知 + 未读私信，按时间倒序合并返回。"""
    user, _ = bot_ctx
    items: list[InboxItemOut] = []

    # --- notifications: replies to bot's comments ---
    replies = list(
        db.scalars(
            select(Comment)
            .where(Comment.reply_to_user_id == user.id, Comment.status == "visible")
            .order_by(Comment.created_at.desc())
            .limit(limit)
        ).all()
    )

    # --- notifications: top-level comments on bot's threads ---
    bot_thread_ids_q = select(Thread.id).where(Thread.author_id == user.id, Thread.status == "published")
    thread_comments = list(
        db.scalars(
            select(Comment)
            .where(
                Comment.thread_id.in_(bot_thread_ids_q),
                Comment.author_id != user.id,
                Comment.depth == 1,
                Comment.status == "visible",
            )
            .order_by(Comment.created_at.desc())
            .limit(limit)
        ).all()
    )

    actor_ids = {int(c.author_id) for c in replies + thread_comments}

    # --- DMs: latest message per conversation ---
    from app.models.dm import DMConversation as _DMConv
    pairs = list(
        db.scalars(
            select(DMPeerPair).where(
                (DMPeerPair.user_low_id == user.id) | (DMPeerPair.user_high_id == user.id)
            )
        ).all()
    )
    conv_ids = [int(p.conversation_id) for p in pairs]
    peer_id_map = {
        int(p.conversation_id): (
            int(p.user_high_id) if int(p.user_low_id) == int(user.id) else int(p.user_low_id)
        )
        for p in pairs
    }
    convs = list(
        db.scalars(
            select(_DMConv)
            .where(_DMConv.id.in_(conv_ids), _DMConv.status == "active", _DMConv.last_message_id.isnot(None))
            .order_by(_DMConv.last_message_at.desc().nullslast())
            .limit(limit)
        ).all()
    ) if conv_ids else []

    dm_last_msgs: dict[int, DMMessage] = {}
    dm_peer_ids: set[int] = set()
    for conv in convs:
        msg = db.get(DMMessage, conv.last_message_id)
        if msg and not msg.is_deleted and int(msg.sender_id) != int(user.id):
            dm_last_msgs[int(conv.id)] = msg
            peer_id = peer_id_map.get(int(conv.id))
            if peer_id:
                dm_peer_ids.add(peer_id)

    actor_ids |= dm_peer_ids
    actor_map = {int(u.id): u for u in db.scalars(select(User).where(User.id.in_(actor_ids))).all()}

    for c in replies:
        actor = actor_map.get(int(c.author_id))
        items.append(InboxItemOut(
            type="reply",
            comment_id=c.id,
            thread_id=c.thread_id,
            actor_username=actor.username if actor else "unknown",
            body_preview=c.body[:200],
            created_at=c.created_at,
        ))
    for c in thread_comments:
        actor = actor_map.get(int(c.author_id))
        items.append(InboxItemOut(
            type="comment",
            comment_id=c.id,
            thread_id=c.thread_id,
            actor_username=actor.username if actor else "unknown",
            body_preview=c.body[:200],
            created_at=c.created_at,
        ))
    for conv in convs:
        msg = dm_last_msgs.get(int(conv.id))
        if not msg:
            continue
        peer_id = peer_id_map.get(int(conv.id))
        peer = actor_map.get(peer_id) if peer_id else None
        items.append(InboxItemOut(
            type="dm",
            conversation_id=conv.id,
            actor_username=peer.username if peer else "unknown",
            body_preview=msg.body[:200],
            created_at=msg.created_at,
        ))

    items.sort(key=lambda x: x.created_at, reverse=True)
    return items[:limit]
