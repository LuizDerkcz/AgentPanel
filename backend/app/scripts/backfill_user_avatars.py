from __future__ import annotations

import argparse
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.append(str(Path(__file__).resolve().parents[2]))

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.user import User, build_default_avatar_url


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill user avatar_url using DiceBear seed=username."
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Update all users, including those with custom avatar_url.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview changes without writing to database.",
    )
    return parser.parse_args()


def should_update(user: User, force_all: bool) -> bool:
    if force_all:
        return True

    current = (user.avatar_url or "").strip()
    if not current:
        return True

    return current.startswith("https://ui-avatars.com/api/")


def main() -> None:
    args = parse_args()
    settings = get_settings()
    engine = create_engine(settings.sqlalchemy_database_uri, pool_pre_ping=True)

    updated = 0
    scanned = 0

    with Session(engine) as db:
        users = list(db.scalars(select(User).order_by(User.id.asc())).all())
        scanned = len(users)

        for user in users:
            if not should_update(user, args.all):
                continue

            new_url = build_default_avatar_url(user.username)
            if user.avatar_url == new_url:
                continue

            user.avatar_url = new_url
            updated += 1

        if args.dry_run:
            db.rollback()
        else:
            db.commit()

    mode = "DRY-RUN" if args.dry_run else "APPLIED"
    print(f"[{mode}] scanned={scanned}, updated={updated}")


if __name__ == "__main__":
    main()
