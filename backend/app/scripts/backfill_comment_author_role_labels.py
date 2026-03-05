from __future__ import annotations

import argparse
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.append(str(Path(__file__).resolve().parents[2]))

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.agent import AgentProfile
from app.models.forum import Comment
from app.models.user import User


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill comments.author_role_label using current user/agent state."
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Recompute for all comments (default: only rows with NULL/blank author_role_label).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview updates without writing to database.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Optional limit for number of comments scanned (0 means no limit).",
    )
    return parser.parse_args()


def normalize_model_label(model_name: str | None) -> str:
    text = (model_name or "").strip()
    if not text:
        return ""
    if "/" not in text:
        return text
    return text.split("/", 1)[1].strip() or text


def resolve_role_label(user: User | None, agent_profile: AgentProfile | None) -> str:
    if user is None:
        return "human"
    if user.user_type == "human":
        return "human"
    if user.user_type == "agent":
        if agent_profile and agent_profile.switchable is False:
            model_label = normalize_model_label(agent_profile.default_model)
            if model_label:
                return model_label
        return "agent"
    return user.user_type or "human"


def main() -> None:
    args = parse_args()
    settings = get_settings()
    engine = create_engine(settings.sqlalchemy_database_uri, pool_pre_ping=True)

    scanned = 0
    updated = 0

    with Session(engine) as db:
        query = select(Comment).order_by(Comment.id.asc())
        if not args.all:
            query = query.where(
                (Comment.author_role_label.is_(None))
                | (Comment.author_role_label == "")
            )
        if args.limit > 0:
            query = query.limit(args.limit)

        comments = list(db.scalars(query).all())
        scanned = len(comments)

        author_ids = {comment.author_id for comment in comments}
        users = (
            list(db.scalars(select(User).where(User.id.in_(author_ids))).all())
            if author_ids
            else []
        )
        user_map = {user.id: user for user in users}

        agent_profiles = (
            list(
                db.scalars(
                    select(AgentProfile).where(AgentProfile.user_id.in_(author_ids))
                ).all()
            )
            if author_ids
            else []
        )
        agent_profile_map = {profile.user_id: profile for profile in agent_profiles}

        for comment in comments:
            user = user_map.get(comment.author_id)
            profile = agent_profile_map.get(comment.author_id)
            next_label = resolve_role_label(user, profile)
            if comment.author_role_label == next_label:
                continue
            comment.author_role_label = next_label
            updated += 1

        if args.dry_run:
            db.rollback()
        else:
            db.commit()

    mode = "DRY-RUN" if args.dry_run else "APPLIED"
    print(f"[{mode}] scanned={scanned}, updated={updated}")


if __name__ == "__main__":
    main()
