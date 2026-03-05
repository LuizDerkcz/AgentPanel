from __future__ import annotations

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.agent import AgentProfile
from app.models.user import User


class AuthorSummaryOut(BaseModel):
    id: int
    username: str
    display_name: str
    avatar_url: str
    is_verified: bool
    user_type: str
    status: str
    switchable: bool = True
    model_name: str | None = None


def build_author_map(
    db: Session, author_ids: set[int | None]
) -> dict[int, AuthorSummaryOut]:
    valid_ids = {aid for aid in author_ids if aid is not None}
    if not valid_ids:
        return {}
    users = list(db.scalars(select(User).where(User.id.in_(valid_ids))).all())
    agent_profiles = list(
        db.scalars(
            select(AgentProfile).where(AgentProfile.user_id.in_(valid_ids))
        ).all()
    )
    agent_profile_map = {profile.user_id: profile for profile in agent_profiles}

    return {
        user.id: AuthorSummaryOut(
            id=user.id,
            username=user.username,
            display_name=user.display_name,
            avatar_url=user.avatar_url,
            is_verified=user.is_verified,
            user_type=user.user_type,
            status=user.status,
            switchable=(
                True
                if user.user_type != "agent"
                else (
                    bool(agent_profile_map.get(user.id).switchable)
                    if agent_profile_map.get(user.id)
                    else True
                )
            ),
            model_name=(
                None
                if user.user_type != "agent"
                else (
                    agent_profile_map.get(user.id).default_model
                    if agent_profile_map.get(user.id)
                    else None
                )
            ),
        )
        for user in users
    }
