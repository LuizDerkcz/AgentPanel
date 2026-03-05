from __future__ import annotations

import argparse
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.append(str(Path(__file__).resolve().parents[2]))

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import hash_password, is_supported_password_hash
from app.models.user import User


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill users with missing/invalid hashed_password."
    )
    parser.add_argument(
        "--password",
        required=True,
        help="Initial plaintext password to set for selected users.",
    )
    parser.add_argument(
        "--usernames",
        default="",
        help="Comma-separated usernames to process (default: all users).",
    )
    parser.add_argument(
        "--force-all",
        action="store_true",
        help="Reset password for all selected users, even if hash is already valid.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview updates without writing to database.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    settings = get_settings()
    engine = create_engine(settings.sqlalchemy_database_uri, pool_pre_ping=True)

    usernames = [u.strip() for u in args.usernames.split(",") if u.strip()]

    scanned = 0
    updated = 0

    with Session(engine) as db:
        query = select(User).order_by(User.id.asc())
        if usernames:
            query = query.where(User.username.in_(usernames))

        users = list(db.scalars(query).all())
        scanned = len(users)

        for user in users:
            if not args.force_all and is_supported_password_hash(user.hashed_password):
                continue

            user.hashed_password = hash_password(args.password)
            updated += 1

        if args.dry_run:
            db.rollback()
        else:
            db.commit()

    mode = "DRY-RUN" if args.dry_run else "APPLIED"
    print(f"[{mode}] scanned={scanned}, updated={updated}")


if __name__ == "__main__":
    main()
