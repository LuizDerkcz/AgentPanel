from datetime import datetime
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps.auth import get_current_demo_user
from app.core.error_codes import NOTIFICATION_NOT_FOUND
from app.core.errors import api_error
from app.db.session import get_db
from app.models.notification import Notification
from app.models.user import User
from app.services.message_outbox import process_pending_events


router = APIRouter(prefix="/notifications", tags=["notifications"])


class NotificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    notification_type: str
    thread_id: int | None = None
    comment_id: int | None = None
    actor_id: int | None = None
    payload: dict
    is_read: bool
    created_at: datetime
    updated_at: datetime


class UnreadCountOut(BaseModel):
    unread_count: int


class MarkAllReadOut(BaseModel):
    updated_count: int


class SystemBroadcastIn(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    body: str = Field(min_length=1, max_length=4000)
    link: str | None = Field(default=None, max_length=500)
    mode: Literal["audience", "users"] = "audience"
    audience: Literal["all", "verified", "human", "agent", "admin"] = "all"
    target_user_ids: list[int] = Field(default_factory=list, max_length=2000)
    broadcast_id: str | None = Field(default=None, max_length=64)


class SystemBroadcastOut(BaseModel):
    broadcast_id: str
    target_users: int
    created_notifications: int


@router.get("/ping")
def ping_notifications() -> dict[str, str]:
    return {"app": "notifications", "status": "ok"}


@router.get("", response_model=list[NotificationOut])
def list_notifications(
    only_unread: bool = Query(default=False),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_demo_user),
) -> list[Notification]:
    process_pending_events(db, limit=200)

    query = select(Notification).where(Notification.user_id == user.id)
    if only_unread:
        query = query.where(Notification.is_read.is_(False))
    query = query.order_by(Notification.created_at.desc(), Notification.id.desc())
    query = query.offset(offset).limit(limit)
    notifications = list(db.scalars(query).all())

    actor_ids = {
        int(item.actor_id)
        for item in notifications
        if item.actor_id is not None and int(item.actor_id) > 0
    }
    actor_map = {}
    if actor_ids:
        actors = list(db.scalars(select(User).where(User.id.in_(actor_ids))).all())
        actor_map = {actor.id: actor for actor in actors}

    for item in notifications:
        payload = dict(item.payload or {})
        actor_id = item.actor_id
        actor = actor_map.get(actor_id) if actor_id is not None else None
        if actor and not payload.get("actor_username"):
            payload["actor_username"] = actor.username
        if actor and not payload.get("actor_display_name"):
            payload["actor_display_name"] = actor.display_name
        item.payload = payload

    return notifications


@router.get("/unread-count", response_model=UnreadCountOut)
def unread_count(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_demo_user),
) -> UnreadCountOut:
    process_pending_events(db, limit=200)

    count = db.scalar(
        select(func.count(Notification.id)).where(
            Notification.user_id == user.id,
            Notification.is_read.is_(False),
        )
    )
    return UnreadCountOut(unread_count=int(count or 0))


@router.post("/{notification_id}/read", response_model=NotificationOut)
def mark_read(
    notification_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_demo_user),
) -> Notification:
    notification = db.get(Notification, notification_id)
    if not notification or notification.user_id != user.id:
        raise api_error(
            status_code=status.HTTP_404_NOT_FOUND,
            code=NOTIFICATION_NOT_FOUND,
            message="Notification not found.",
        )

    if not notification.is_read:
        notification.is_read = True
        db.commit()
        db.refresh(notification)

    return notification


@router.post("/read-all", response_model=MarkAllReadOut)
def mark_all_read(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_demo_user),
) -> MarkAllReadOut:
    notifications = list(
        db.scalars(
            select(Notification).where(
                Notification.user_id == user.id,
                Notification.is_read.is_(False),
            )
        ).all()
    )

    for item in notifications:
        item.is_read = True

    if notifications:
        db.commit()

    return MarkAllReadOut(updated_count=len(notifications))


@router.post("/system-broadcast", response_model=SystemBroadcastOut)
def create_system_broadcast(
    payload: SystemBroadcastIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_demo_user),
) -> SystemBroadcastOut:
    if str(user.user_type) != "admin":
        raise api_error(
            status_code=status.HTTP_403_FORBIDDEN,
            code="NOTIFICATION_BROADCAST_FORBIDDEN",
            message="Only admin can broadcast system notifications.",
        )

    normalized_broadcast_id = (
        str(payload.broadcast_id).strip() if payload.broadcast_id else uuid4().hex
    )

    user_query = select(User.id).where(User.status == "active")
    normalized_target_user_ids: list[int] = []
    if payload.mode == "users":
        normalized_target_user_ids = sorted(
            {
                int(user_id)
                for user_id in payload.target_user_ids
                if isinstance(user_id, int) and int(user_id) > 0
            }
        )
        if not normalized_target_user_ids:
            raise api_error(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                code="NOTIFICATION_BROADCAST_TARGET_REQUIRED",
                message="target_user_ids is required when mode=users.",
            )
        user_query = user_query.where(User.id.in_(normalized_target_user_ids))
    else:
        if payload.audience == "verified":
            user_query = user_query.where(User.is_verified.is_(True))
        elif payload.audience == "human":
            user_query = user_query.where(User.user_type == "human")
        elif payload.audience == "agent":
            user_query = user_query.where(User.user_type == "agent")
        elif payload.audience == "admin":
            user_query = user_query.where(User.user_type == "admin")

    target_user_ids = [int(item) for item in db.scalars(user_query).all()]
    if not target_user_ids:
        return SystemBroadcastOut(
            broadcast_id=normalized_broadcast_id,
            target_users=0,
            created_notifications=0,
        )

    duplicated_user_ids = set(
        int(item)
        for item in db.scalars(
            select(Notification.user_id).where(
                Notification.notification_type == "system",
                Notification.user_id.in_(target_user_ids),
                Notification.payload["broadcast_id"].astext == normalized_broadcast_id,
            )
        ).all()
    )

    payload_base = {
        "broadcast_id": normalized_broadcast_id,
        "title": payload.title.strip(),
        "body": payload.body.strip(),
        "link": (payload.link or "").strip() or None,
        "mode": payload.mode,
        "audience": payload.audience,
        "target_user_ids": normalized_target_user_ids,
        "source": "system_broadcast",
    }

    rows = [
        Notification(
            user_id=target_user_id,
            notification_type="system",
            payload=payload_base,
            is_read=False,
        )
        for target_user_id in target_user_ids
        if target_user_id not in duplicated_user_ids
    ]

    if rows:
        db.add_all(rows)
        db.commit()

    return SystemBroadcastOut(
        broadcast_id=normalized_broadcast_id,
        target_users=len(target_user_ids),
        created_notifications=len(rows),
    )
