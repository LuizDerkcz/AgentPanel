from __future__ import annotations

import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.append(str(Path(__file__).resolve().parents[2]))

from sqlalchemy import create_engine, text

from app.core.config import get_settings


def main() -> None:
    settings = get_settings()
    engine = create_engine(settings.sqlalchemy_database_uri)

    tables = [
        "users",
        "categories",
        "threads",
        "comments",
        "likes",
        "agents",
        "agent_actions",
        "notifications",
    ]

    with engine.connect() as conn:
        for table in tables:
            count = conn.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()
            print(f"{table}: {count}")


if __name__ == "__main__":
    main()
