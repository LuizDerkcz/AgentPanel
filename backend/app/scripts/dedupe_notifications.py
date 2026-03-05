# This script is designed to identify and remove duplicate notifications from the database. It groups notifications by a deduplication key, which is primarily based on the event_id in the payload. If event_id is not available, it falls back to a composite key of other relevant fields. The script then identifies groups of duplicates, determines which one to keep based on read status and creation time, and optionally deletes the duplicates from the database. By default, it runs in dry-run mode, allowing you to preview the duplicates before actually deleting them.

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.append(str(Path(__file__).resolve().parents[2]))

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.notification import Notification


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Deduplicate historical notifications by payload.event_id first, "
            "then by a fallback key. Default mode is dry-run."
        )
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually delete duplicates. Without this flag, only prints a preview.",
    )
    parser.add_argument(
        "--limit-preview",
        type=int,
        default=20,
        help="How many duplicate groups to preview in output.",
    )
    return parser.parse_args()


def build_group_key(notification: Notification) -> tuple:
    payload = notification.payload or {}
    event_id = str(payload.get("event_id") or "").strip()
    if event_id:
        return ("event_id", int(notification.user_id), event_id)

    event_type = str(payload.get("event_type") or "").strip() or None
    return (
        "fallback",
        int(notification.user_id),
        str(notification.notification_type),
        notification.thread_id,
        notification.comment_id,
        notification.actor_id,
        event_type,
    )


def pick_keep_id(group: list[Notification]) -> int:
    ordered = sorted(
        group,
        key=lambda item: (bool(not item.is_read), item.created_at, item.id),
        reverse=True,
    )
    return int(ordered[0].id)


def main() -> None:
    args = parse_args()

    with SessionLocal() as db:
        notifications = list(
            db.scalars(
                select(Notification).order_by(
                    Notification.user_id.asc(),
                    Notification.created_at.desc(),
                    Notification.id.desc(),
                )
            ).all()
        )

        grouped: dict[tuple, list[Notification]] = defaultdict(list)
        for notification in notifications:
            grouped[build_group_key(notification)].append(notification)

        duplicate_groups: list[tuple[tuple, list[Notification]]] = []
        delete_ids: list[int] = []

        for key, group in grouped.items():
            if len(group) <= 1:
                continue
            keep_id = pick_keep_id(group)
            to_delete = [int(item.id) for item in group if int(item.id) != keep_id]
            if not to_delete:
                continue
            duplicate_groups.append((key, group))
            delete_ids.extend(to_delete)

        print(f"Scanned notifications: {len(notifications)}")
        print(f"Duplicate groups: {len(duplicate_groups)}")
        print(f"Duplicate rows to delete: {len(delete_ids)}")

        preview_count = max(0, int(args.limit_preview))
        if preview_count:
            print("\nPreview groups:")
            for index, (key, group) in enumerate(
                duplicate_groups[:preview_count], start=1
            ):
                keep_id = pick_keep_id(group)
                print(f"{index}. key={key} size={len(group)} keep_id={keep_id}")

        if not args.apply:
            print("\nDry-run only. Re-run with --apply to delete duplicates.")
            return

        if not delete_ids:
            print("\nNo duplicate rows to delete.")
            return

        for notification_id in delete_ids:
            row = db.get(Notification, notification_id)
            if row:
                db.delete(row)

        db.commit()
        print(f"\nDeleted duplicate rows: {len(delete_ids)}")


if __name__ == "__main__":
    main()
