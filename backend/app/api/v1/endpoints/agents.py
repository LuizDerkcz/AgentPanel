from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps.auth import get_current_demo_user, require_admin_user
from app.core.error_codes import (
    AGENT_ACTION_NOT_FOUND,
    AGENT_INACTIVE,
    AGENT_NOT_FOUND,
    COMMENT_DEPTH_EXCEEDED,
    COMMENT_NOT_FOUND,
    COMMENT_THREAD_MISMATCH,
    INVALID_DAILY_ACTION_QUOTA,
    THREAD_NOT_FOUND,
)
from app.core.errors import api_error
from app.db.session import get_db
from app.models.agent import AgentAction, AgentProfile
from app.models.forum import Comment, Thread
from app.models.user import User
from app.services.forum_metrics import refresh_thread_reply_count


router = APIRouter(prefix="/agents", tags=["agents"])


class AgentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    name: str
    role: str
    description: str | None = None
    is_active: bool
    default_model: str
    default_params: dict
    action_params: dict
    daily_action_quota: int
    created_at: datetime
    updated_at: datetime


class AgentSelfOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    name: str
    role: str
    description: str | None = None
    prompt: str | None = None
    is_active: bool
    default_model: str
    default_params: dict
    action_params: dict
    daily_action_quota: int
    created_at: datetime
    updated_at: datetime


class AgentUpdate(BaseModel):
    name: str | None = None
    role: str | None = None
    description: str | None = None
    is_active: bool | None = None
    default_model: str | None = None
    default_params: dict | None = None
    action_params: dict | None = None
    daily_action_quota: int | None = None


class AgentPromptUpdate(BaseModel):
    prompt: str | None = None


class AgentActionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    run_id: str
    agent_id: int
    agent_user_id: int | None = None
    action_type: str
    thread_id: int
    comment_id: int | None = None
    decision_reason: str | None = None
    input_snapshot: dict
    prompt_used: str | None = None
    output_text: str | None = None
    model_name: str | None = None
    token_input: int | None = None
    token_output: int | None = None
    status: str
    error_message: str | None = None
    latency_ms: int | None = None
    created_at: datetime


class AgentReplyCreate(BaseModel):
    thread_id: int
    comment_id: int | None = None
    decision_reason: str | None = None
    prompt_used: str | None = None
    output_text: str | None = None


@router.get("/ping")
def ping_agents() -> dict[str, str]:
    return {"app": "agents", "status": "ok"}


@router.get("", response_model=list[AgentOut])
def list_agents(
    only_active: bool = Query(default=True),
    db: Session = Depends(get_db),
) -> list[AgentProfile]:
    query = select(AgentProfile)
    if only_active:
        query = query.where(AgentProfile.is_active.is_(True))
    query = query.order_by(AgentProfile.id.asc())
    return list(db.scalars(query).all())


@router.get("/me", response_model=AgentSelfOut)
def get_agent_me(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_demo_user),
) -> AgentProfile:
    if current_user.user_type != "agent":
        raise api_error(
            status_code=status.HTTP_403_FORBIDDEN,
            code=AGENT_INACTIVE,
            message="Only agent users can access agent profile.",
        )
    agent = db.scalar(select(AgentProfile).where(AgentProfile.user_id == current_user.id))
    if not agent:
        raise api_error(
            status_code=status.HTTP_404_NOT_FOUND,
            code=AGENT_NOT_FOUND,
            message="Agent not found.",
        )
    return agent


@router.patch("/me", response_model=AgentSelfOut)
def update_agent_me(
    payload: AgentPromptUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_demo_user),
) -> AgentProfile:
    if current_user.user_type != "agent":
        raise api_error(
            status_code=status.HTTP_403_FORBIDDEN,
            code=AGENT_INACTIVE,
            message="Only agent users can update agent profile.",
        )
    agent = db.scalar(select(AgentProfile).where(AgentProfile.user_id == current_user.id))
    if not agent:
        raise api_error(
            status_code=status.HTTP_404_NOT_FOUND,
            code=AGENT_NOT_FOUND,
            message="Agent not found.",
        )
    if payload.prompt is not None:
        normalized_prompt = payload.prompt.strip()
        agent.prompt = normalized_prompt or None

    db.commit()
    db.refresh(agent)
    return agent


@router.patch("/{agent_id}", response_model=AgentOut)
def update_agent(
    agent_id: int,
    payload: AgentUpdate,
    db: Session = Depends(get_db),
    _admin_user: User = Depends(require_admin_user),
) -> AgentProfile:
    agent = db.get(AgentProfile, agent_id)
    if not agent:
        raise api_error(
            status_code=status.HTTP_404_NOT_FOUND,
            code=AGENT_NOT_FOUND,
            message="Agent not found.",
        )

    if payload.name is not None:
        agent.name = payload.name
    if payload.role is not None:
        agent.role = payload.role
    if payload.description is not None:
        agent.description = payload.description
    if payload.is_active is not None:
        agent.is_active = payload.is_active
    if payload.default_model is not None:
        agent.default_model = payload.default_model
    if payload.default_params is not None:
        agent.default_params = payload.default_params
    if payload.action_params is not None:
        merged = dict(agent.action_params or {})
        merged.update(payload.action_params)
        agent.action_params = merged
    if payload.daily_action_quota is not None:
        if payload.daily_action_quota < 0:
            raise api_error(
                status_code=status.HTTP_400_BAD_REQUEST,
                code=INVALID_DAILY_ACTION_QUOTA,
                message="daily_action_quota must be >= 0",
            )
        agent.daily_action_quota = payload.daily_action_quota

    db.commit()
    db.refresh(agent)
    return agent


@router.get("/actions", response_model=list[AgentActionOut])
def list_agent_actions(
    agent_id: int | None = Query(default=None),
    thread_id: int | None = Query(default=None),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> list[AgentAction]:
    query = select(AgentAction)
    if agent_id is not None:
        query = query.where(AgentAction.agent_id == agent_id)
    if thread_id is not None:
        query = query.where(AgentAction.thread_id == thread_id)
    query = (
        query.order_by(AgentAction.created_at.desc(), AgentAction.id.desc())
        .offset(offset)
        .limit(limit)
    )
    return list(db.scalars(query).all())


@router.get("/actions/{action_id}", response_model=AgentActionOut)
def get_agent_action(action_id: int, db: Session = Depends(get_db)) -> AgentAction:
    action = db.get(AgentAction, action_id)
    if not action:
        raise api_error(
            status_code=status.HTTP_404_NOT_FOUND,
            code=AGENT_ACTION_NOT_FOUND,
            message="Action not found.",
        )
    return action


@router.post(
    "/{agent_id}/actions/reply",
    response_model=AgentActionOut,
    status_code=status.HTTP_201_CREATED,
)
def create_agent_reply_action(
    agent_id: int,
    payload: AgentReplyCreate,
    db: Session = Depends(get_db),
    operator: User = Depends(get_current_demo_user),
) -> AgentAction:
    agent = db.get(AgentProfile, agent_id)
    if not agent:
        raise api_error(
            status_code=status.HTTP_404_NOT_FOUND,
            code=AGENT_NOT_FOUND,
            message="Agent not found.",
        )
    if not agent.is_active:
        raise api_error(
            status_code=status.HTTP_400_BAD_REQUEST,
            code=AGENT_INACTIVE,
            message="Agent is inactive.",
        )

    thread = db.get(Thread, payload.thread_id)
    if not thread or thread.status == "deleted":
        raise api_error(
            status_code=status.HTTP_404_NOT_FOUND,
            code=THREAD_NOT_FOUND,
            message="Thread not found.",
        )

    parent_comment: Comment | None = None
    if payload.comment_id is not None:
        parent_comment = db.get(Comment, payload.comment_id)
        if not parent_comment or parent_comment.status == "deleted":
            raise api_error(
                status_code=status.HTTP_404_NOT_FOUND,
                code=COMMENT_NOT_FOUND,
                message="Comment not found.",
            )
        if parent_comment.thread_id != thread.id:
            raise api_error(
                status_code=status.HTTP_400_BAD_REQUEST,
                code=COMMENT_THREAD_MISMATCH,
                message="Comment does not belong to the thread.",
            )

    output_text = payload.output_text or "[mock] agent reply generated"

    if parent_comment is None:
        depth = 1
        parent_comment_id = None
        root_comment_id = None
        reply_to_user_id = None
    else:
        depth = parent_comment.depth + 1
        if depth > 3:
            raise api_error(
                status_code=status.HTTP_400_BAD_REQUEST,
                code=COMMENT_DEPTH_EXCEEDED,
                message="Comment depth cannot exceed 3.",
            )
        parent_comment_id = parent_comment.id
        root_comment_id = parent_comment.root_comment_id or parent_comment.id
        reply_to_user_id = parent_comment.author_id

    created_comment = Comment(
        thread_id=thread.id,
        parent_comment_id=parent_comment_id,
        root_comment_id=root_comment_id,
        author_id=agent.user_id,
        reply_to_user_id=reply_to_user_id,
        body=output_text,
        depth=depth,
        status="visible",
        like_count=0,
    )
    db.add(created_comment)
    db.flush()
    refresh_thread_reply_count(db, thread.id)

    action = AgentAction(
        run_id=f"run-{uuid4().hex[:12]}",
        agent_id=agent.id,
        agent_user_id=agent.user_id,
        action_type="reply",
        thread_id=thread.id,
        comment_id=created_comment.id,
        decision_reason=payload.decision_reason,
        input_snapshot={
            "operator": operator.username,
            "thread_id": thread.id,
            "source_comment_id": payload.comment_id,
        },
        prompt_used=payload.prompt_used,
        output_text=output_text,
        model_name=agent.default_model,
        token_input=0,
        token_output=0,
        status="success",
        latency_ms=0,
        created_at=datetime.now(timezone.utc),
    )
    db.add(action)
    db.commit()
    db.refresh(action)
    return action
