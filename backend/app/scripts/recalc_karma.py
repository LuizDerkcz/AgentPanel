"""Recalculate karma for all users based on existing likes and answer votes.

Usage:
    cd backend && python -m app.scripts.recalc_karma
"""
from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.append(str(Path(__file__).resolve().parents[2]))

from sqlalchemy import select, update

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.forum import AnswerVote, Comment, Like, Thread
from app.models.user import User


def main() -> None:
    db = SessionLocal()
    karma_map: dict[int, int] = defaultdict(int)

    # ── Likes → +1 per like (exclude self-likes) ──
    all_likes = db.scalars(select(Like)).all()
    for like in all_likes:
        if like.target_type == "thread":
            target = db.get(Thread, like.target_id)
            author_id = target.author_id if target else None
        else:
            target = db.get(Comment, like.target_id)
            author_id = target.author_id if target else None

        if author_id and author_id != like.user_id:
            karma_map[author_id] += 1

    print(f"Processed {len(all_likes)} likes")

    # ── Answer votes → +1 for up, -1 for down (exclude self-votes) ──
    all_votes = db.scalars(select(AnswerVote)).all()
    for vote in all_votes:
        comment = db.get(Comment, vote.comment_id)
        author_id = comment.author_id if comment else None

        if author_id and author_id != vote.user_id:
            karma_map[author_id] += vote.vote  # +1 or -1

    print(f"Processed {len(all_votes)} answer votes")

    # ── Reset all karma to 0, then apply calculated values ──
    db.execute(update(User).values(karma=0))

    updated = 0
    for user_id, karma in karma_map.items():
        db.execute(
            update(User).where(User.id == user_id).values(karma=karma)
        )
        updated += 1

    db.commit()
    print(f"Updated karma for {updated} users")

    # ── Show top 10 ──
    top_users = db.scalars(
        select(User).order_by(User.karma.desc()).limit(10)
    ).all()
    print("\nTop 10 users by karma:")
    for u in top_users:
        print(f"  {u.username:30s} karma={u.karma}")

    db.close()


if __name__ == "__main__":
    main()
