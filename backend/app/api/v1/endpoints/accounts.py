from datetime import datetime

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.api.deps.auth import get_current_demo_user
from app.core.error_codes import (
    CONTENT_CONTAINS_SENSITIVE_WORDS,
    FOLLOW_ALREADY_EXISTS,
    FOLLOW_NOT_FOUND,
    FOLLOW_SELF_NOT_ALLOWED,
    USER_NOT_FOUND,
)
from app.services.content_filter import find_hits
from app.core.errors import api_error
from app.db.session import get_db
from app.models.agent import AgentProfile
from app.models.forum import AnswerVote, Category, Comment, Like, Thread
from app.models.user import User, UserFollow, build_default_avatar_url


router = APIRouter(prefix="/accounts", tags=["accounts"])


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_type: str
    username: str
    display_name: str
    bio: str | None = None
    email: str | None = None
    avatar_url: str
    is_verified: bool
    status: str
    lang: str = "zh"
    switchable: bool = True
    model_name: str | None = None
    created_at: datetime
    updated_at: datetime


class UserBriefOut(BaseModel):
    id: int
    username: str
    display_name: str
    user_type: str
    status: str
    avatar_url: str
    is_verified: bool
    switchable: bool = True
    model_name: str | None = None


class SimilarUserOut(BaseModel):
    id: int
    username: str
    display_name: str
    user_type: str
    status: str
    avatar_url: str
    is_verified: bool
    switchable: bool = True
    model_name: str | None = None
    likes_count: int = 0
    followers_count: int = 0
    tags: list[str] = Field(default_factory=list)


class ProfileStatsOut(BaseModel):
    posts_count: int
    comments_count: int
    likes_count: int
    followers_count: int
    following_count: int
    is_following: bool = False


class FollowStateOut(BaseModel):
    username: str
    is_following: bool
    followers_count: int
    following_count: int


class ProfileCommentPreviewOut(BaseModel):
    id: int
    body: str
    created_at: datetime
    author: UserBriefOut | None = None


class ProfilePostOut(BaseModel):
    id: int
    category_id: int
    category_name: str
    title: str
    abstract: str | None = None
    body: str
    like_count: int
    reply_count: int
    created_at: datetime
    last_activity_at: datetime
    comments_preview: list[ProfileCommentPreviewOut]


class ProfileCommentOut(BaseModel):
    id: int
    thread_id: int
    thread_title: str
    body: str
    depth: int
    like_count: int
    upvote_count: int
    created_at: datetime


class ProfileLikeOut(BaseModel):
    id: int
    target_type: str
    target_id: int
    created_at: datetime
    thread_id: int
    thread_title: str
    item_title: str
    author: UserBriefOut | None = None
    score: int


class UserProfileAggregateOut(BaseModel):
    user: UserOut
    stats: ProfileStatsOut
    tags: list[str]
    posts: list[ProfilePostOut]
    comments: list[ProfileCommentOut]
    likes: list[ProfileLikeOut]
    similar_users: list[SimilarUserOut]


class FollowUserItem(BaseModel):
    username: str
    display_name: str
    avatar_url: str
    bio: str | None = None
    followers_count: int = 0
    user_type: str = "human"
    is_verified: bool = False


def _build_agent_profile_map(
    db: Session, user_ids: set[int] | list[int] | tuple[int, ...]
) -> dict[int, AgentProfile]:
    valid_ids = {int(item) for item in user_ids if item is not None}
    if not valid_ids:
        return {}
    profiles = list(
        db.scalars(
            select(AgentProfile).where(AgentProfile.user_id.in_(valid_ids))
        ).all()
    )
    return {profile.user_id: profile for profile in profiles}


def _resolve_identity_fields(
    user: User, agent_profile: AgentProfile | None
) -> tuple[bool, str | None]:
    if user.user_type != "agent":
        return True, None
    if not agent_profile:
        return True, None
    return bool(agent_profile.switchable), agent_profile.default_model


def _resolve_visible_bio(user: User, agent_profile: AgentProfile | None) -> str | None:
    if user.user_type != "agent":
        return user.bio
    if not agent_profile:
        return user.bio
    if agent_profile.switchable:
        return user.bio
    return agent_profile.description or user.bio


def _to_user_out(user: User, agent_profile: AgentProfile | None = None) -> UserOut:
    switchable, model_name = _resolve_identity_fields(user, agent_profile)
    return UserOut.model_validate(
        {
            "id": user.id,
            "user_type": user.user_type,
            "username": user.username,
            "display_name": user.display_name,
            "bio": _resolve_visible_bio(user, agent_profile),
            "email": user.email,
            "avatar_url": user.avatar_url,
            "is_verified": user.is_verified,
            "status": user.status,
            "lang": user.lang or "zh",
            "switchable": switchable,
            "model_name": model_name,
            "created_at": user.created_at,
            "updated_at": user.updated_at,
        }
    )


def _to_user_brief(
    user: User | None, agent_profile: AgentProfile | None = None
) -> UserBriefOut | None:
    if user is None:
        return None
    switchable, model_name = _resolve_identity_fields(user, agent_profile)
    return UserBriefOut(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        user_type=user.user_type,
        status=user.status,
        avatar_url=user.avatar_url,
        is_verified=user.is_verified,
        switchable=switchable,
        model_name=model_name,
    )


class MeUpdateInput(BaseModel):
    bio: str | None = Field(default=None, max_length=2000)
    user_type: str | None = Field(default=None, pattern="^(human|agent)$")
    avatar_url: str | None = Field(default=None, max_length=200)
    lang: str | None = Field(default=None, pattern="^(zh|en)$")


@router.get("/ping")
def ping_accounts() -> dict[str, str]:
    return {"app": "accounts", "status": "ok"}


@router.get("/users", response_model=list[UserOut])
def list_users(
    user_type: str | None = Query(default=None),
    include_inactive: bool = Query(default=False),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=1000),
    db: Session = Depends(get_db),
) -> list[UserOut]:
    query = select(User)

    if user_type is not None:
        query = query.where(User.user_type == user_type)
    if not include_inactive:
        query = query.where(User.status == "active")

    query = (
        query.order_by(User.created_at.desc(), User.id.desc())
        .offset(offset)
        .limit(limit)
    )
    users = list(db.scalars(query).all())
    agent_profile_map = _build_agent_profile_map(db, [user.id for user in users])
    return [_to_user_out(user, agent_profile_map.get(user.id)) for user in users]


@router.get("/users/{user_id}", response_model=UserOut)
def get_user_by_id(
    user_id: int,
    include_inactive: bool = Query(default=False),
    db: Session = Depends(get_db),
) -> UserOut:
    user = db.get(User, user_id)
    if not user or (not include_inactive and user.status != "active"):
        raise api_error(
            status_code=status.HTTP_404_NOT_FOUND,
            code=USER_NOT_FOUND,
            message="User not found.",
        )
    agent_profile = (
        db.scalar(select(AgentProfile).where(AgentProfile.user_id == user.id))
        if user.user_type == "agent"
        else None
    )
    return _to_user_out(user, agent_profile)


@router.get("/me", response_model=UserOut)
def get_me(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_demo_user),
) -> UserOut:
    agent_profile = (
        db.scalar(select(AgentProfile).where(AgentProfile.user_id == current_user.id))
        if current_user.user_type == "agent"
        else None
    )
    return _to_user_out(current_user, agent_profile)


@router.patch("/me", response_model=UserOut)
def update_me(
    payload: MeUpdateInput,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_demo_user),
) -> UserOut:
    if payload.avatar_url is not None:
        stripped = payload.avatar_url.strip()
        current_user.avatar_url = (
            stripped if stripped else build_default_avatar_url(current_user.username)
        )
    if payload.bio is not None:
        normalized_bio = payload.bio.strip()
        if normalized_bio and current_user.user_type == "human":
            hits = find_hits([normalized_bio])
            if hits:
                raise api_error(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    code=CONTENT_CONTAINS_SENSITIVE_WORDS,
                    message="个人简介包含敏感词，请修改后重新保存",
                    details={"hits": hits},
                )
        current_user.bio = normalized_bio or None
    if payload.user_type is not None:
        if current_user.user_type == "admin":
            raise api_error(
                status_code=status.HTTP_403_FORBIDDEN,
                code="ADMIN_USER_TYPE_LOCKED",
                message="Admin user type cannot be changed.",
            )
        current_user.user_type = payload.user_type
        if payload.user_type == "agent":
            agent_profile = db.scalar(
                select(AgentProfile).where(AgentProfile.user_id == current_user.id)
            )
            if agent_profile is None:
                display_name = current_user.display_name or current_user.username
                agent_profile = AgentProfile(
                    user_id=current_user.id,
                    name=display_name,
                    role="Agent",
                    description=current_user.bio,
                    is_active=True,
                )
                db.add(agent_profile)

    if payload.lang is not None:
        current_user.lang = payload.lang

    db.add(current_user)
    db.commit()
    db.refresh(current_user)
    agent_profile = (
        db.scalar(select(AgentProfile).where(AgentProfile.user_id == current_user.id))
        if current_user.user_type == "agent"
        else None
    )
    return _to_user_out(current_user, agent_profile)


@router.get("/users/{username}/profile", response_model=UserProfileAggregateOut)
def get_user_profile_aggregate(
    username: str,
    include_inactive: bool = Query(default=False),
    similar_limit: int = Query(default=5, ge=0, le=20),
    viewer_username: str | None = Query(default=None),
    posts_offset: int = Query(default=0, ge=0),
    posts_limit: int = Query(default=20, ge=1, le=50),
    posts_sort: str = Query(default="time"),
    comments_offset: int = Query(default=0, ge=0),
    comments_limit: int = Query(default=20, ge=1, le=50),
    likes_offset: int = Query(default=0, ge=0),
    likes_limit: int = Query(default=20, ge=1, le=50),
    db: Session = Depends(get_db),
) -> UserProfileAggregateOut:
    user = db.scalar(select(User).where(User.username == username))
    if not user or (not include_inactive and user.status != "active"):
        raise api_error(
            status_code=status.HTTP_404_NOT_FOUND,
            code=USER_NOT_FOUND,
            message="User not found.",
        )

    category_list = list(db.scalars(select(Category)).all())
    category_name_map = {category.id: category.name for category in category_list}

    agent_profile: AgentProfile | None = None
    if user.user_type == "agent":
        agent_profile = db.scalar(
            select(AgentProfile).where(AgentProfile.user_id == user.id)
        )

    total_posts_count = (
        db.scalar(
            select(func.count(Thread.id)).where(
                Thread.author_id == user.id,
                Thread.status != "deleted",
            )
        )
        or 0
    )

    posts_order = (
        Thread.like_count.desc()
        if posts_sort == "votes"
        else Thread.created_at.desc()
    )
    posts = list(
        db.scalars(
            select(Thread)
            .where(Thread.author_id == user.id, Thread.status != "deleted")
            .order_by(posts_order, Thread.id.desc())
            .offset(posts_offset)
            .limit(posts_limit)
        ).all()
    )
    post_ids = [post.id for post in posts]

    post_comments = []
    if post_ids:
        ranked_comments_subquery = (
            select(
                Comment.id.label("comment_id"),
                func.row_number()
                .over(
                    partition_by=Comment.thread_id,
                    order_by=(Comment.created_at.desc(), Comment.id.desc()),
                )
                .label("row_num"),
            )
            .where(Comment.thread_id.in_(post_ids), Comment.status != "deleted")
            .subquery()
        )

        preview_comment_ids = list(
            db.scalars(
                select(ranked_comments_subquery.c.comment_id).where(
                    ranked_comments_subquery.c.row_num <= 3
                )
            ).all()
        )

        if preview_comment_ids:
            post_comments = list(
                db.scalars(
                    select(Comment)
                    .where(Comment.id.in_(preview_comment_ids))
                    .order_by(
                        Comment.thread_id.asc(),
                        Comment.created_at.desc(),
                        Comment.id.desc(),
                    )
                ).all()
            )

    comments_by_thread: dict[int, list[Comment]] = {}
    for comment in post_comments:
        comments_by_thread.setdefault(comment.thread_id, []).append(comment)

    user_ids_from_post_comments = {comment.author_id for comment in post_comments}
    post_comment_agent_profile_map = _build_agent_profile_map(
        db, user_ids_from_post_comments
    )
    post_comment_author_map = (
        {
            user_item.id: user_item
            for user_item in db.scalars(
                select(User).where(User.id.in_(user_ids_from_post_comments))
            ).all()
        }
        if user_ids_from_post_comments
        else {}
    )

    posts_out = []
    for post in posts:
        preview_comments = comments_by_thread.get(post.id, [])[:3]
        posts_out.append(
            ProfilePostOut(
                id=post.id,
                category_id=post.category_id,
                category_name=category_name_map.get(post.category_id, "General"),
                title=post.title,
                abstract=post.abstract,
                body=post.body,
                like_count=post.like_count,
                reply_count=post.reply_count,
                created_at=post.created_at,
                last_activity_at=post.last_activity_at,
                comments_preview=[
                    ProfileCommentPreviewOut(
                        id=item.id,
                        body=item.body,
                        created_at=item.created_at,
                        author=_to_user_brief(
                            post_comment_author_map.get(item.author_id),
                            post_comment_agent_profile_map.get(item.author_id),
                        ),
                    )
                    for item in preview_comments
                ],
            )
        )

    total_comments_count = (
        db.scalar(
            select(func.count(Comment.id)).where(
                Comment.author_id == user.id,
                Comment.status != "deleted",
            )
        )
        or 0
    )

    user_comments = list(
        db.scalars(
            select(Comment)
            .where(Comment.author_id == user.id, Comment.status != "deleted")
            .order_by(Comment.created_at.desc(), Comment.id.desc())
            .offset(comments_offset)
            .limit(comments_limit)
        ).all()
    )
    user_comment_thread_ids = {comment.thread_id for comment in user_comments}
    user_comment_thread_map = (
        {
            thread.id: thread
            for thread in db.scalars(
                select(Thread).where(Thread.id.in_(user_comment_thread_ids))
            ).all()
        }
        if user_comment_thread_ids
        else {}
    )

    comments_out = [
        ProfileCommentOut(
            id=comment.id,
            thread_id=comment.thread_id,
            thread_title=(
                user_comment_thread_map.get(comment.thread_id).title
                if user_comment_thread_map.get(comment.thread_id)
                else f"Thread #{comment.thread_id}"
            ),
            body=comment.body,
            depth=comment.depth,
            like_count=comment.like_count,
            upvote_count=comment.upvote_count,
            created_at=comment.created_at,
        )
        for comment in user_comments
    ]

    likes_on_threads = db.scalar(
        select(func.count(Like.id))
        .join(Thread, and_(Like.target_id == Thread.id, Like.target_type == "thread"))
        .where(Thread.author_id == user.id)
    ) or 0
    likes_on_comments = db.scalar(
        select(func.count(Like.id))
        .join(Comment, and_(Like.target_id == Comment.id, Like.target_type == "comment"))
        .where(Comment.author_id == user.id)
    ) or 0
    upvotes_on_comments = db.scalar(
        select(func.count(AnswerVote.id))
        .join(Comment, AnswerVote.comment_id == Comment.id)
        .where(Comment.author_id == user.id, AnswerVote.vote == 1)
    ) or 0
    downvotes_on_comments = db.scalar(
        select(func.count(AnswerVote.id))
        .join(Comment, AnswerVote.comment_id == Comment.id)
        .where(Comment.author_id == user.id, AnswerVote.vote == -1)
    ) or 0
    total_likes_count = likes_on_threads + likes_on_comments + upvotes_on_comments - downvotes_on_comments

    likes = list(
        db.scalars(
            select(Like)
            .where(Like.user_id == user.id)
            .order_by(Like.created_at.desc(), Like.id.desc())
            .offset(likes_offset)
            .limit(likes_limit)
        ).all()
    )
    liked_thread_ids = {
        like.target_id for like in likes if like.target_type == "thread"
    }
    liked_comment_ids = {
        like.target_id for like in likes if like.target_type == "comment"
    }

    liked_thread_map = (
        {
            thread.id: thread
            for thread in db.scalars(
                select(Thread).where(Thread.id.in_(liked_thread_ids))
            ).all()
        }
        if liked_thread_ids
        else {}
    )

    liked_comment_map = (
        {
            comment.id: comment
            for comment in db.scalars(
                select(Comment).where(Comment.id.in_(liked_comment_ids))
            ).all()
        }
        if liked_comment_ids
        else {}
    )

    related_thread_ids_from_comments = {
        comment.thread_id for comment in liked_comment_map.values()
    }
    related_thread_map = (
        {
            thread.id: thread
            for thread in db.scalars(
                select(Thread).where(Thread.id.in_(related_thread_ids_from_comments))
            ).all()
        }
        if related_thread_ids_from_comments
        else {}
    )

    like_author_ids = {thread.author_id for thread in liked_thread_map.values()} | {
        comment.author_id for comment in liked_comment_map.values()
    }
    like_author_agent_profile_map = _build_agent_profile_map(db, like_author_ids)
    like_author_map = (
        {
            user_item.id: user_item
            for user_item in db.scalars(
                select(User).where(User.id.in_(like_author_ids))
            ).all()
        }
        if like_author_ids
        else {}
    )

    likes_out: list[ProfileLikeOut] = []
    for like in likes:
        if like.target_type == "thread":
            thread = liked_thread_map.get(like.target_id)
            if not thread:
                continue
            likes_out.append(
                ProfileLikeOut(
                    id=like.id,
                    target_type=like.target_type,
                    target_id=like.target_id,
                    created_at=like.created_at,
                    thread_id=thread.id,
                    thread_title=thread.title,
                    item_title=thread.title,
                    author=_to_user_brief(
                        like_author_map.get(thread.author_id),
                        like_author_agent_profile_map.get(thread.author_id),
                    ),
                    score=thread.like_count,
                )
            )
        else:
            comment = liked_comment_map.get(like.target_id)
            if not comment:
                continue
            parent_thread = related_thread_map.get(comment.thread_id)
            likes_out.append(
                ProfileLikeOut(
                    id=like.id,
                    target_type=like.target_type,
                    target_id=like.target_id,
                    created_at=like.created_at,
                    thread_id=comment.thread_id,
                    thread_title=(
                        parent_thread.title
                        if parent_thread
                        else f"Thread #{comment.thread_id}"
                    ),
                    item_title=(
                        (comment.body[:80] + "...")
                        if len(comment.body) > 80
                        else comment.body
                    ),
                    author=_to_user_brief(
                        like_author_map.get(comment.author_id),
                        like_author_agent_profile_map.get(comment.author_id),
                    ),
                    score=(
                        comment.upvote_count
                        if comment.depth == 1
                        else comment.like_count
                    ),
                )
            )

    # Append upvoted comments (answer_votes with vote=1) to likes_out
    upvotes = list(
        db.scalars(
            select(AnswerVote)
            .where(AnswerVote.user_id == user.id, AnswerVote.vote == 1)
            .order_by(AnswerVote.created_at.desc(), AnswerVote.id.desc())
        ).all()
    )
    if upvotes:
        upvoted_comment_ids = {av.comment_id for av in upvotes}
        upvoted_comment_map = {
            c.id: c
            for c in db.scalars(
                select(Comment).where(Comment.id.in_(upvoted_comment_ids))
            ).all()
        }
        upvoted_thread_ids = {c.thread_id for c in upvoted_comment_map.values()}
        upvoted_thread_map = {
            t.id: t
            for t in db.scalars(
                select(Thread).where(Thread.id.in_(upvoted_thread_ids))
            ).all()
        }
        upvote_author_ids = {c.author_id for c in upvoted_comment_map.values()}
        upvote_author_map = {
            u.id: u
            for u in db.scalars(
                select(User).where(User.id.in_(upvote_author_ids))
            ).all()
        }
        for av in upvotes:
            comment = upvoted_comment_map.get(av.comment_id)
            if not comment:
                continue
            parent_thread = upvoted_thread_map.get(comment.thread_id)
            likes_out.append(
                ProfileLikeOut(
                    id=-(av.id),
                    target_type="comment_vote",
                    target_id=comment.id,
                    created_at=av.created_at,
                    thread_id=comment.thread_id,
                    thread_title=(
                        parent_thread.title
                        if parent_thread
                        else f"Thread #{comment.thread_id}"
                    ),
                    item_title=(
                        (comment.body[:80] + "...") if len(comment.body) > 80 else comment.body
                    ),
                    author=_to_user_brief(upvote_author_map.get(comment.author_id)),
                    score=comment.upvote_count,
                )
            )
        likes_out.sort(key=lambda x: x.created_at, reverse=True)

    tag_counter: dict[str, int] = {}
    all_post_category_ids = list(
        db.scalars(
            select(Thread.category_id).where(
                Thread.author_id == user.id,
                Thread.status != "deleted",
            )
        ).all()
    )
    for category_id in all_post_category_ids:
        tag_name = category_name_map.get(category_id, "General")
        tag_counter[tag_name] = tag_counter.get(tag_name, 0) + 1

    # 追加评论行为的类别
    comment_category_ids = db.scalars(
        select(Thread.category_id)
        .join(Comment, Comment.thread_id == Thread.id)
        .where(
            Comment.author_id == user.id,
            Comment.status != "deleted",
            Thread.status != "deleted",
        )
    ).all()
    for category_id in comment_category_ids:
        tag_name = category_name_map.get(category_id, "General")
        tag_counter[tag_name] = tag_counter.get(tag_name, 0) + 1

    user_tag_set = set(tag_counter.keys())

    tags = [
        name
        for name, _ in sorted(
            tag_counter.items(), key=lambda item: item[1], reverse=True
        )[:3]
    ]

    # 候选池：按 followers 数取高质量候选，不够则补充最新注册用户
    candidate_limit = min(60, similar_limit * 12)
    follower_count_rows = db.execute(
        select(UserFollow.followee_user_id, func.count(UserFollow.id))
        .where(
            UserFollow.followee_user_id.in_(
                select(User.id).where(
                    User.username != user.username, User.status == "active"
                )
            )
        )
        .group_by(UserFollow.followee_user_id)
        .order_by(func.count(UserFollow.id).desc())
        .limit(candidate_limit)
    ).all()
    candidate_ids = [uid for uid, _ in follower_count_rows]
    if len(candidate_ids) < candidate_limit:
        existing = set(candidate_ids)
        extra = list(db.scalars(
            select(User.id)
            .where(
                User.username != user.username,
                User.status == "active",
                User.id.notin_(existing),
            )
            .order_by(User.created_at.desc())
            .limit(candidate_limit - len(candidate_ids))
        ).all())
        candidate_ids += extra

    similar_users = list(
        db.scalars(select(User).where(User.id.in_(candidate_ids))).all()
    )
    similar_user_ids = [similar_user.id for similar_user in similar_users]
    similar_user_agent_profile_map = _build_agent_profile_map(db, similar_user_ids)

    similar_likes_map: dict[int, int] = {}
    similar_followers_map: dict[int, int] = {}
    similar_tags_map: dict[int, list[str]] = {}

    if similar_user_ids:
        thread_like_rows = db.execute(
            select(Thread.author_id, func.count(Like.id))
            .join(Thread, and_(Like.target_id == Thread.id, Like.target_type == "thread"))
            .where(Thread.author_id.in_(similar_user_ids))
            .group_by(Thread.author_id)
        ).all()
        similar_likes_map = {
            int(uid): int(cnt) for uid, cnt in thread_like_rows if uid is not None
        }
        comment_like_rows = db.execute(
            select(Comment.author_id, func.count(Like.id))
            .join(Comment, and_(Like.target_id == Comment.id, Like.target_type == "comment"))
            .where(Comment.author_id.in_(similar_user_ids))
            .group_by(Comment.author_id)
        ).all()
        for uid, cnt in comment_like_rows:
            if uid is not None:
                similar_likes_map[int(uid)] = similar_likes_map.get(int(uid), 0) + int(cnt)
        upvote_rows = db.execute(
            select(Comment.author_id, func.count(AnswerVote.id))
            .join(Comment, AnswerVote.comment_id == Comment.id)
            .where(Comment.author_id.in_(similar_user_ids), AnswerVote.vote == 1)
            .group_by(Comment.author_id)
        ).all()
        for uid, cnt in upvote_rows:
            if uid is not None:
                similar_likes_map[int(uid)] = similar_likes_map.get(int(uid), 0) + int(cnt)

        follower_rows = db.execute(
            select(UserFollow.followee_user_id, func.count(UserFollow.id))
            .where(UserFollow.followee_user_id.in_(similar_user_ids))
            .group_by(UserFollow.followee_user_id)
        ).all()
        similar_followers_map = {
            int(user_id): int(count)
            for user_id, count in follower_rows
            if user_id is not None
        }

        category_counter_by_user: dict[int, dict[str, int]] = {}

        def add_user_tag(user_id: int | None, category_id: int | None) -> None:
            if user_id is None:
                return
            tag_name = category_name_map.get(category_id, "General")
            per_user_counter = category_counter_by_user.setdefault(int(user_id), {})
            per_user_counter[tag_name] = per_user_counter.get(tag_name, 0) + 1

        # 1) Categories from authored threads
        authored_rows = db.execute(
            select(Thread.author_id, Thread.category_id).where(
                Thread.author_id.in_(similar_user_ids),
                Thread.status != "deleted",
            )
        ).all()
        for author_id, category_id in authored_rows:
            add_user_tag(author_id, category_id)

        # 2) Categories from threads where user has commented
        comment_rows = db.execute(
            select(Comment.author_id, Thread.category_id)
            .join(Thread, Thread.id == Comment.thread_id)
            .where(
                Comment.author_id.in_(similar_user_ids),
                Comment.status != "deleted",
                Thread.status != "deleted",
            )
        ).all()
        for author_id, category_id in comment_rows:
            add_user_tag(author_id, category_id)

        # 3) Categories from liked targets (thread or comment->thread)
        like_rows = db.execute(
            select(Like.user_id, Like.target_type, Like.target_id).where(
                Like.user_id.in_(similar_user_ids)
            )
        ).all()
        liked_thread_ids = {
            int(target_id)
            for _, target_type, target_id in like_rows
            if target_type == "thread"
        }
        liked_comment_ids = {
            int(target_id)
            for _, target_type, target_id in like_rows
            if target_type == "comment"
        }

        liked_thread_category_map: dict[int, int] = {}
        if liked_thread_ids:
            liked_thread_rows = db.execute(
                select(Thread.id, Thread.category_id).where(
                    Thread.id.in_(liked_thread_ids),
                    Thread.status != "deleted",
                )
            ).all()
            liked_thread_category_map = {
                int(thread_id): int(category_id)
                for thread_id, category_id in liked_thread_rows
            }

        liked_comment_thread_map: dict[int, int] = {}
        if liked_comment_ids:
            liked_comment_rows = db.execute(
                select(Comment.id, Comment.thread_id).where(
                    Comment.id.in_(liked_comment_ids),
                    Comment.status != "deleted",
                )
            ).all()
            liked_comment_thread_map = {
                int(comment_id): int(thread_id)
                for comment_id, thread_id in liked_comment_rows
            }

        if liked_comment_thread_map:
            liked_comment_thread_ids = list(set(liked_comment_thread_map.values()))
            liked_comment_thread_rows = db.execute(
                select(Thread.id, Thread.category_id).where(
                    Thread.id.in_(liked_comment_thread_ids),
                    Thread.status != "deleted",
                )
            ).all()
            liked_comment_thread_category_map = {
                int(thread_id): int(category_id)
                for thread_id, category_id in liked_comment_thread_rows
            }
        else:
            liked_comment_thread_category_map = {}

        for user_id, target_type, target_id in like_rows:
            if target_type == "thread":
                add_user_tag(user_id, liked_thread_category_map.get(int(target_id)))
            elif target_type == "comment":
                thread_id = liked_comment_thread_map.get(int(target_id))
                if thread_id is not None:
                    add_user_tag(
                        user_id,
                        liked_comment_thread_category_map.get(thread_id),
                    )

        similar_tags_map = {
            author_id: [
                tag_name
                for tag_name, _ in sorted(
                    counter.items(), key=lambda item: item[1], reverse=True
                )[:3]
            ]
            for author_id, counter in category_counter_by_user.items()
        }

    # 打分排序：兴趣标签重叠数优先，同分按 followers 数降序
    if user_tag_set:
        def _similar_score(u: User) -> tuple:
            overlap = len(set(similar_tags_map.get(u.id, [])) & user_tag_set)
            followers = similar_followers_map.get(u.id, 0)
            return (-overlap, -followers)
        similar_users = sorted(similar_users, key=_similar_score)[:similar_limit]
    else:
        similar_users = sorted(
            similar_users,
            key=lambda u: -similar_followers_map.get(u.id, 0),
        )[:similar_limit]

    followers_count = (
        db.scalar(
            select(func.count(UserFollow.id)).where(
                UserFollow.followee_user_id == user.id
            )
        )
        or 0
    )
    following_count = (
        db.scalar(
            select(func.count(UserFollow.id)).where(
                UserFollow.follower_user_id == user.id
            )
        )
        or 0
    )

    is_following = False
    if viewer_username and viewer_username != user.username:
        viewer_user = db.scalar(
            select(User).where(
                User.username == viewer_username, User.status == "active"
            )
        )
        if viewer_user:
            is_following = (
                db.scalar(
                    select(UserFollow.id).where(
                        UserFollow.follower_user_id == viewer_user.id,
                        UserFollow.followee_user_id == user.id,
                    )
                )
                is not None
            )

    return UserProfileAggregateOut(
        user=_to_user_out(user, agent_profile),
        stats=ProfileStatsOut(
            posts_count=total_posts_count,
            comments_count=total_comments_count,
            likes_count=total_likes_count,
            followers_count=followers_count,
            following_count=following_count,
            is_following=is_following,
        ),
        tags=tags,
        posts=posts_out,
        comments=comments_out,
        likes=likes_out,
        similar_users=[
            SimilarUserOut(
                id=similar_user.id,
                username=similar_user.username,
                display_name=similar_user.display_name,
                user_type=similar_user.user_type,
                status=similar_user.status,
                avatar_url=similar_user.avatar_url,
                is_verified=similar_user.is_verified,
                switchable=_resolve_identity_fields(
                    similar_user,
                    similar_user_agent_profile_map.get(similar_user.id),
                )[0],
                model_name=_resolve_identity_fields(
                    similar_user,
                    similar_user_agent_profile_map.get(similar_user.id),
                )[1],
                likes_count=similar_likes_map.get(similar_user.id, 0),
                followers_count=similar_followers_map.get(similar_user.id, 0),
                tags=similar_tags_map.get(similar_user.id, []),
            )
            for similar_user in similar_users
        ],
    )


@router.post("/users/{username}/follow", response_model=FollowStateOut)
def follow_user(
    username: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_demo_user),
) -> FollowStateOut:
    target_user = db.scalar(select(User).where(User.username == username))
    if not target_user or target_user.status != "active":
        raise api_error(
            status_code=status.HTTP_404_NOT_FOUND,
            code=USER_NOT_FOUND,
            message="User not found.",
        )

    if current_user.id == target_user.id:
        raise api_error(
            status_code=status.HTTP_400_BAD_REQUEST,
            code=FOLLOW_SELF_NOT_ALLOWED,
            message="Cannot follow yourself.",
        )

    existing_follow = db.scalar(
        select(UserFollow).where(
            UserFollow.follower_user_id == current_user.id,
            UserFollow.followee_user_id == target_user.id,
        )
    )
    if existing_follow:
        raise api_error(
            status_code=status.HTTP_409_CONFLICT,
            code=FOLLOW_ALREADY_EXISTS,
            message="Already following this user.",
        )

    db.add(
        UserFollow(
            follower_user_id=current_user.id,
            followee_user_id=target_user.id,
        )
    )
    db.commit()

    followers_count = (
        db.scalar(
            select(func.count(UserFollow.id)).where(
                UserFollow.followee_user_id == target_user.id
            )
        )
        or 0
    )
    following_count = (
        db.scalar(
            select(func.count(UserFollow.id)).where(
                UserFollow.follower_user_id == target_user.id
            )
        )
        or 0
    )

    return FollowStateOut(
        username=target_user.username,
        is_following=True,
        followers_count=followers_count,
        following_count=following_count,
    )


@router.delete("/users/{username}/follow", response_model=FollowStateOut)
def unfollow_user(
    username: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_demo_user),
) -> FollowStateOut:
    target_user = db.scalar(select(User).where(User.username == username))
    if not target_user or target_user.status != "active":
        raise api_error(
            status_code=status.HTTP_404_NOT_FOUND,
            code=USER_NOT_FOUND,
            message="User not found.",
        )

    if current_user.id == target_user.id:
        raise api_error(
            status_code=status.HTTP_400_BAD_REQUEST,
            code=FOLLOW_SELF_NOT_ALLOWED,
            message="Cannot unfollow yourself.",
        )

    existing_follow = db.scalar(
        select(UserFollow).where(
            UserFollow.follower_user_id == current_user.id,
            UserFollow.followee_user_id == target_user.id,
        )
    )
    if not existing_follow:
        raise api_error(
            status_code=status.HTTP_404_NOT_FOUND,
            code=FOLLOW_NOT_FOUND,
            message="Follow relation not found.",
        )

    db.delete(existing_follow)
    db.commit()

    followers_count = (
        db.scalar(
            select(func.count(UserFollow.id)).where(
                UserFollow.followee_user_id == target_user.id
            )
        )
        or 0
    )
    following_count = (
        db.scalar(
            select(func.count(UserFollow.id)).where(
                UserFollow.follower_user_id == target_user.id
            )
        )
        or 0
    )

    return FollowStateOut(
        username=target_user.username,
        is_following=False,
        followers_count=followers_count,
        following_count=following_count,
    )


@router.get("/users/{username}/followers", response_model=list[FollowUserItem])
def list_user_followers(
    username: str,
    limit: int = Query(default=100, le=200),
    db: Session = Depends(get_db),
) -> list[FollowUserItem]:
    target = db.scalar(select(User).where(User.username == username, User.status == "active"))
    if not target:
        raise api_error(USER_NOT_FOUND, status.HTTP_404_NOT_FOUND)

    rows = db.execute(
        select(User)
        .join(UserFollow, UserFollow.follower_user_id == User.id)
        .where(UserFollow.followee_user_id == target.id)
        .order_by(UserFollow.created_at.desc())
        .limit(limit)
    ).scalars().all()

    ids = [u.id for u in rows]
    count_map: dict[int, int] = {}
    if ids:
        count_rows = db.execute(
            select(UserFollow.followee_user_id, func.count(UserFollow.id))
            .where(UserFollow.followee_user_id.in_(ids))
            .group_by(UserFollow.followee_user_id)
        ).all()
        count_map = {int(uid): int(cnt) for uid, cnt in count_rows}

    return [
        FollowUserItem(
            username=u.username,
            display_name=u.display_name or u.username,
            avatar_url=u.avatar_url or build_default_avatar_url(u.username),
            bio=u.bio,
            followers_count=count_map.get(u.id, 0),
            user_type=u.user_type,
            is_verified=bool(u.is_verified),
        )
        for u in rows
    ]


@router.get("/users/{username}/following", response_model=list[FollowUserItem])
def list_user_following(
    username: str,
    limit: int = Query(default=100, le=200),
    db: Session = Depends(get_db),
) -> list[FollowUserItem]:
    target = db.scalar(select(User).where(User.username == username, User.status == "active"))
    if not target:
        raise api_error(USER_NOT_FOUND, status.HTTP_404_NOT_FOUND)

    rows = db.execute(
        select(User)
        .join(UserFollow, UserFollow.followee_user_id == User.id)
        .where(UserFollow.follower_user_id == target.id)
        .order_by(UserFollow.created_at.desc())
        .limit(limit)
    ).scalars().all()

    ids = [u.id for u in rows]
    count_map: dict[int, int] = {}
    if ids:
        count_rows = db.execute(
            select(UserFollow.followee_user_id, func.count(UserFollow.id))
            .where(UserFollow.followee_user_id.in_(ids))
            .group_by(UserFollow.followee_user_id)
        ).all()
        count_map = {int(uid): int(cnt) for uid, cnt in count_rows}

    return [
        FollowUserItem(
            username=u.username,
            display_name=u.display_name or u.username,
            avatar_url=u.avatar_url or build_default_avatar_url(u.username),
            bio=u.bio,
            followers_count=count_map.get(u.id, 0),
            user_type=u.user_type,
            is_verified=bool(u.is_verified),
        )
        for u in rows
    ]
