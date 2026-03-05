from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps.auth import get_current_demo_user
from app.core.error_codes import (
    DM_CONVERSATION_NOT_FOUND,
    DM_EMPTY_MESSAGE,
    DM_PARTICIPANT_FORBIDDEN,
    DM_PEER_NOT_FOUND,
    DM_PEER_REQUIRED,
    DM_SELF_CHAT_NOT_ALLOWED,
)
from app.core.errors import api_error
from app.db.session import get_db
from app.models.agent import AgentProfile
from app.models.dm import DMConversation, DMMessage, DMParticipant, DMPeerPair
from app.models.user import User


router = APIRouter(prefix="/dm", tags=["dm"])


class DMUserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    display_name: str
    avatar_url: str
    user_type: str
    is_verified: bool
    is_assistant: bool = False


class DMMessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    conversation_id: int
    sender_user_id: int
    msg_type: str
    body: str
    body_lang: str | None = None
    reply_to_message_id: int | None = None
    is_edited: bool
    is_deleted: bool
    client_msg_id: str | None = None
    meta: dict | None = None
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None
    sender: DMUserOut | None = None


class DMConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    type: str
    title: str | None = None
    owner_user_id: int | None = None
    last_message_id: int | None = None
    last_message_at: datetime | None = None
    status: str
    unread_count: int = 0
    peer_user: DMUserOut | None = None
    last_message_preview: str | None = None
    created_at: datetime
    updated_at: datetime


class DMConversationCreateIn(BaseModel):
    peer_user_id: int | None = Field(default=None, ge=1)
    peer_username: str | None = Field(default=None, min_length=1, max_length=150)


class DMMessageCreateIn(BaseModel):
    body: str = Field(min_length=1, max_length=8000)
    body_lang: str | None = Field(default=None, max_length=16)
    client_msg_id: str | None = Field(default=None, max_length=64)


class DMReadMarkOut(BaseModel):
    conversation_id: int
    last_read_message_id: int | None = None
    unread_count: int


def _resolve_assistant_user_ids(db: Session, user_ids: set[int]) -> set[int]:
    if not user_ids:
        return set()
    rows = list(
        db.scalars(
            select(AgentProfile).where(
                AgentProfile.user_id.in_(user_ids),
                AgentProfile.is_active.is_(True),
            )
        ).all()
    )
    return {
        int(item.user_id)
        for item in rows
        if "assistant" in str(item.role or "").strip().lower()
    }


def _to_dm_user_out(
    user: User | None, assistant_user_ids: set[int] | None = None
) -> DMUserOut | None:
    if not user:
        return None
    assistant_ids = assistant_user_ids or set()
    return DMUserOut(
        id=int(user.id),
        username=user.username,
        display_name=user.display_name,
        avatar_url=user.avatar_url,
        user_type=user.user_type,
        is_verified=bool(user.is_verified),
        is_assistant=int(user.id) in assistant_ids,
    )


def _extract_preview(body: str | None, limit: int = 100) -> str:
    normalized = " ".join(str(body or "").split())
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[:limit]}…"


def _get_conversation_or_404(db: Session, conversation_id: int) -> DMConversation:
    conversation = db.get(DMConversation, conversation_id)
    if not conversation or conversation.status == "deleted":
        raise api_error(
            status_code=status.HTTP_404_NOT_FOUND,
            code=DM_CONVERSATION_NOT_FOUND,
            message="Conversation not found.",
        )
    return conversation


def _get_participant(
    db: Session, conversation_id: int, user_id: int
) -> DMParticipant | None:
    return db.scalar(
        select(DMParticipant).where(
            DMParticipant.conversation_id == conversation_id,
            DMParticipant.user_id == user_id,
            DMParticipant.left_at.is_(None),
        )
    )


def _ensure_participant(
    db: Session, conversation_id: int, user_id: int
) -> DMParticipant:
    participant = _get_participant(db, conversation_id, user_id)
    if participant:
        return participant
    raise api_error(
        status_code=status.HTTP_403_FORBIDDEN,
        code=DM_PARTICIPANT_FORBIDDEN,
        message="You are not a participant of this conversation.",
    )


@router.get("/ping")
def ping_dm() -> dict[str, str]:
    return {"app": "dm", "status": "ok"}


@router.post("/conversations", response_model=DMConversationOut)
def create_or_get_direct_conversation(
    payload: DMConversationCreateIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_demo_user),
) -> DMConversationOut:
    target_user: User | None = None

    if payload.peer_user_id:
        target_user = db.get(User, payload.peer_user_id)
    elif payload.peer_username:
        target_user = db.scalar(
            select(User).where(User.username == payload.peer_username.strip())
        )
    else:
        raise api_error(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            code=DM_PEER_REQUIRED,
            message="peer_user_id or peer_username is required.",
        )

    if not target_user or target_user.status != "active":
        raise api_error(
            status_code=status.HTTP_404_NOT_FOUND,
            code=DM_PEER_NOT_FOUND,
            message="Peer user not found.",
        )

    if int(target_user.id) == int(current_user.id):
        raise api_error(
            status_code=status.HTTP_400_BAD_REQUEST,
            code=DM_SELF_CHAT_NOT_ALLOWED,
            message="Cannot start a direct conversation with yourself.",
        )

    low_id = min(int(current_user.id), int(target_user.id))
    high_id = max(int(current_user.id), int(target_user.id))

    pair = db.get(DMPeerPair, (low_id, high_id))
    if pair:
        conversation = _get_conversation_or_404(db, int(pair.conversation_id))
    else:
        now = datetime.now(timezone.utc)
        conversation = DMConversation(
            type="direct",
            owner_user_id=int(current_user.id),
            status="active",
            last_message_at=now,
        )
        db.add(conversation)
        db.flush()

        db.add_all(
            [
                DMParticipant(
                    conversation_id=int(conversation.id),
                    user_id=int(current_user.id),
                    role="owner",
                ),
                DMParticipant(
                    conversation_id=int(conversation.id),
                    user_id=int(target_user.id),
                    role="member",
                ),
                DMPeerPair(
                    user_low_id=low_id,
                    user_high_id=high_id,
                    conversation_id=int(conversation.id),
                ),
            ]
        )
        db.commit()
        db.refresh(conversation)

    assistant_user_ids = _resolve_assistant_user_ids(
        db, {int(current_user.id), int(target_user.id)}
    )

    return DMConversationOut(
        id=int(conversation.id),
        type=conversation.type,
        title=conversation.title,
        owner_user_id=conversation.owner_user_id,
        last_message_id=conversation.last_message_id,
        last_message_at=conversation.last_message_at,
        status=conversation.status,
        unread_count=0,
        peer_user=_to_dm_user_out(target_user, assistant_user_ids),
        last_message_preview=None,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
    )


@router.get("/conversations", response_model=list[DMConversationOut])
def list_conversations(
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_demo_user),
) -> list[DMConversationOut]:
    participant_rows = list(
        db.scalars(
            select(DMParticipant)
            .where(
                DMParticipant.user_id == current_user.id,
                DMParticipant.left_at.is_(None),
            )
            .order_by(DMParticipant.updated_at.desc())
            .offset(offset)
            .limit(limit)
        ).all()
    )

    if not participant_rows:
        return []

    conversation_ids = [int(row.conversation_id) for row in participant_rows]
    conversations = list(
        db.scalars(
            select(DMConversation).where(
                DMConversation.id.in_(conversation_ids),
                DMConversation.status != "deleted",
            )
        ).all()
    )
    conversation_map = {int(item.id): item for item in conversations}

    other_participants = db.execute(
        select(DMParticipant.conversation_id, DMParticipant.user_id).where(
            DMParticipant.conversation_id.in_(conversation_ids),
            DMParticipant.user_id != current_user.id,
            DMParticipant.left_at.is_(None),
        )
    ).all()
    peer_user_ids = {int(row.user_id) for row in other_participants}
    peer_map = {}
    if peer_user_ids:
        peer_users = list(
            db.scalars(select(User).where(User.id.in_(peer_user_ids))).all()
        )
        peer_map = {int(user.id): user for user in peer_users}

    assistant_user_ids = _resolve_assistant_user_ids(
        db, {int(current_user.id), *peer_user_ids}
    )

    last_message_ids = {
        int(conv.last_message_id)
        for conv in conversations
        if conv.last_message_id is not None
    }
    last_message_map = {}
    if last_message_ids:
        last_messages = list(
            db.scalars(
                select(DMMessage).where(DMMessage.id.in_(last_message_ids))
            ).all()
        )
        last_message_map = {int(msg.id): msg for msg in last_messages}

    peer_by_conversation = {}
    for row in other_participants:
        if int(row.conversation_id) not in peer_by_conversation:
            peer_by_conversation[int(row.conversation_id)] = peer_map.get(
                int(row.user_id)
            )

    response: list[DMConversationOut] = []
    for participant in participant_rows:
        conversation = conversation_map.get(int(participant.conversation_id))
        if not conversation:
            continue

        last_read_id = int(participant.last_read_message_id or 0)
        unread_count = int(
            db.scalar(
                select(func.count(DMMessage.id)).where(
                    DMMessage.conversation_id == conversation.id,
                    DMMessage.id > last_read_id,
                    DMMessage.sender_user_id != current_user.id,
                    DMMessage.is_deleted.is_(False),
                )
            )
            or 0
        )

        peer = peer_by_conversation.get(int(conversation.id))
        last_message = (
            last_message_map.get(int(conversation.last_message_id))
            if conversation.last_message_id is not None
            else None
        )

        response.append(
            DMConversationOut(
                id=int(conversation.id),
                type=conversation.type,
                title=conversation.title,
                owner_user_id=conversation.owner_user_id,
                last_message_id=conversation.last_message_id,
                last_message_at=conversation.last_message_at,
                status=conversation.status,
                unread_count=unread_count,
                peer_user=_to_dm_user_out(peer, assistant_user_ids),
                last_message_preview=_extract_preview(
                    last_message.body if last_message else None
                ),
                created_at=conversation.created_at,
                updated_at=conversation.updated_at,
            )
        )

    response.sort(
        key=lambda item: item.last_message_at or item.created_at,
        reverse=True,
    )
    return response


@router.get(
    "/conversations/{conversation_id}/messages", response_model=list[DMMessageOut]
)
def list_messages(
    conversation_id: int,
    before_id: int | None = Query(default=None, ge=1),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_demo_user),
) -> list[DMMessageOut]:
    _ = _get_conversation_or_404(db, conversation_id)
    _ = _ensure_participant(db, conversation_id, int(current_user.id))

    query = select(DMMessage).where(DMMessage.conversation_id == conversation_id)
    if before_id is not None:
        query = query.where(DMMessage.id < before_id)
    query = query.order_by(DMMessage.id.desc()).limit(limit)

    messages = list(db.scalars(query).all())
    messages.reverse()

    sender_ids = {int(message.sender_user_id) for message in messages}
    sender_map = {}
    if sender_ids:
        senders = list(db.scalars(select(User).where(User.id.in_(sender_ids))).all())
        sender_map = {int(sender.id): sender for sender in senders}

    assistant_user_ids = _resolve_assistant_user_ids(
        db, {int(current_user.id), *sender_ids}
    )

    return [
        DMMessageOut(
            id=int(message.id),
            conversation_id=int(message.conversation_id),
            sender_user_id=int(message.sender_user_id),
            msg_type=message.msg_type,
            body=message.body,
            body_lang=message.body_lang,
            reply_to_message_id=message.reply_to_message_id,
            is_edited=bool(message.is_edited),
            is_deleted=bool(message.is_deleted),
            client_msg_id=message.client_msg_id,
            meta=message.meta,
            created_at=message.created_at,
            updated_at=message.updated_at,
            deleted_at=message.deleted_at,
            sender=_to_dm_user_out(
                sender_map.get(int(message.sender_user_id)),
                assistant_user_ids,
            ),
        )
        for message in messages
    ]


@router.post("/conversations/{conversation_id}/messages", response_model=DMMessageOut)
def send_message(
    conversation_id: int,
    payload: DMMessageCreateIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_demo_user),
) -> DMMessageOut:
    conversation = _get_conversation_or_404(db, conversation_id)
    _ = _ensure_participant(db, conversation_id, int(current_user.id))

    body = payload.body.strip()
    if not body:
        raise api_error(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            code=DM_EMPTY_MESSAGE,
            message="Message body cannot be empty.",
        )

    existing = None
    if payload.client_msg_id:
        existing = db.scalar(
            select(DMMessage).where(
                DMMessage.conversation_id == conversation_id,
                DMMessage.client_msg_id == payload.client_msg_id,
            )
        )

    assistant_user_ids = _resolve_assistant_user_ids(db, {int(current_user.id)})

    if existing:
        sender = db.get(User, existing.sender_user_id)
        return DMMessageOut(
            id=int(existing.id),
            conversation_id=int(existing.conversation_id),
            sender_user_id=int(existing.sender_user_id),
            msg_type=existing.msg_type,
            body=existing.body,
            body_lang=existing.body_lang,
            reply_to_message_id=existing.reply_to_message_id,
            is_edited=bool(existing.is_edited),
            is_deleted=bool(existing.is_deleted),
            client_msg_id=existing.client_msg_id,
            meta=existing.meta,
            created_at=existing.created_at,
            updated_at=existing.updated_at,
            deleted_at=existing.deleted_at,
            sender=_to_dm_user_out(sender, assistant_user_ids),
        )

    message = DMMessage(
        conversation_id=conversation_id,
        sender_user_id=int(current_user.id),
        msg_type="text",
        body=body,
        body_lang=payload.body_lang,
        client_msg_id=payload.client_msg_id,
    )
    db.add(message)
    db.flush()

    conversation.last_message_id = int(message.id)
    conversation.last_message_at = message.created_at

    db.commit()
    db.refresh(message)

    return DMMessageOut(
        id=int(message.id),
        conversation_id=int(message.conversation_id),
        sender_user_id=int(message.sender_user_id),
        msg_type=message.msg_type,
        body=message.body,
        body_lang=message.body_lang,
        reply_to_message_id=message.reply_to_message_id,
        is_edited=bool(message.is_edited),
        is_deleted=bool(message.is_deleted),
        client_msg_id=message.client_msg_id,
        meta=message.meta,
        created_at=message.created_at,
        updated_at=message.updated_at,
        deleted_at=message.deleted_at,
        sender=_to_dm_user_out(current_user, assistant_user_ids),
    )


@router.post("/conversations/{conversation_id}/read", response_model=DMReadMarkOut)
def mark_conversation_read(
    conversation_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_demo_user),
) -> DMReadMarkOut:
    _ = _get_conversation_or_404(db, conversation_id)
    participant = _ensure_participant(db, conversation_id, int(current_user.id))

    max_other_message_id = db.scalar(
        select(func.max(DMMessage.id)).where(
            DMMessage.conversation_id == conversation_id,
            DMMessage.sender_user_id != current_user.id,
            DMMessage.is_deleted.is_(False),
        )
    )
    next_read_id = int(max_other_message_id or 0)

    current_read_id = int(participant.last_read_message_id or 0)
    if next_read_id > current_read_id:
        participant.last_read_message_id = next_read_id
        participant.last_read_at = datetime.now(timezone.utc)
        db.commit()
    else:
        db.flush()

    unread_count = int(
        db.scalar(
            select(func.count(DMMessage.id)).where(
                DMMessage.conversation_id == conversation_id,
                DMMessage.id > int(participant.last_read_message_id or 0),
                DMMessage.sender_user_id != current_user.id,
                DMMessage.is_deleted.is_(False),
            )
        )
        or 0
    )

    return DMReadMarkOut(
        conversation_id=conversation_id,
        last_read_message_id=participant.last_read_message_id,
        unread_count=unread_count,
    )
