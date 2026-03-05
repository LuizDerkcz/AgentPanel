from __future__ import annotations

import argparse
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.append(str(Path(__file__).resolve().parents[2]))

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.services.message_outbox import process_pending_events


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Process pending outbox events and project notifications."
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=200,
        help="Max pending events to process in one run.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    settings = get_settings()
    engine = create_engine(settings.sqlalchemy_database_uri, pool_pre_ping=True)

    with Session(engine) as db:
        processed = process_pending_events(db, limit=args.limit)

    print(f"processed={processed}")


if __name__ == "__main__":
    main()
