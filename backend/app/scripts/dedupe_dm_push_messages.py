from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.append(str(Path(__file__).resolve().parents[2]))

from sqlalchemy import select

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.dm import DMMessage


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Deduplicate DM push messages (meta.push_kind=interest_top10). "
            "Default mode is dry-run for current cycle window."
        )
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually delete duplicates. Without this flag, only prints preview.",
    )
    parser.add_argument(
        "--all-time",
        action="store_true",
        help="Deduplicate across all history instead of current cycle window.",
    )
    parser.add_argument(
        "--limit-preview",
        type=int,
        default=20,
        help="How many duplicate groups to preview in output.",
    )
    return parser.parse_args()


def resolve_cycle_window(
    start_time: str | None, interval_minutes: int
) -> tuple[datetime, datetime]:
    now_local = datetime.now().astimezone()
    hour = 0
    minute = 0

    text = (start_time or "").strip()
    if text:
        try:
            hour_text, minute_text = text.split(":", 1)
            hour = max(0, min(23, int(hour_text)))
            minute = max(0, min(59, int(minute_text)))
        except Exception:
            hour = 0
            minute = 0

    interval = max(1, int(interval_minutes))
    daily_anchor = now_local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    anchor = daily_anchor
    while anchor > now_local:
        anchor = anchor - timedelta(minutes=interval)

    delta_minutes = int((now_local - anchor).total_seconds() // 60)
    windows_since_anchor = max(0, delta_minutes // interval)
    cycle_start_local = anchor + timedelta(minutes=windows_since_anchor * interval)
    cycle_end_local = cycle_start_local + timedelta(minutes=interval)

    return cycle_start_local.astimezone(timezone.utc), cycle_end_local.astimezone(
        timezone.utc
    )


def pick_keep_id(group: list[DMMessage]) -> int:
    ordered = sorted(
        group,
        key=lambda item: (item.created_at, int(item.id)),
        reverse=True,
    )
    return int(ordered[0].id)


def main() -> None:
    args = parse_args()
    settings = get_settings()

    cycle_start_utc, cycle_end_utc = resolve_cycle_window(
        settings.dm_push_assistant_start_time,
        settings.dm_push_assistant_interval_minutes,
    )

    with SessionLocal() as db:
        stmt = select(DMMessage).where(
            DMMessage.meta["push_kind"].astext == "interest_top10"
        )
        if not args.all_time:
            stmt = stmt.where(
                DMMessage.created_at >= cycle_start_utc,
                DMMessage.created_at < cycle_end_utc,
            )

        messages = list(
            db.scalars(
                stmt.order_by(
                    DMMessage.conversation_id.asc(),
                    DMMessage.sender_user_id.asc(),
                    DMMessage.created_at.desc(),
                    DMMessage.id.desc(),
                )
            ).all()
        )

        grouped: dict[tuple[int, int], list[DMMessage]] = defaultdict(list)
        for message in messages:
            key = (int(message.conversation_id), int(message.sender_user_id))
            grouped[key].append(message)

        duplicate_groups: list[tuple[tuple[int, int], list[DMMessage]]] = []
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

        scope = "all history" if args.all_time else "current cycle"
        print(f"Scope: {scope}")
        if not args.all_time:
            print(
                f"Cycle window UTC: [{cycle_start_utc.isoformat()} -> {cycle_end_utc.isoformat()})"
            )
        print(f"Scanned push messages: {len(messages)}")
        print(f"Duplicate groups: {len(duplicate_groups)}")
        print(f"Duplicate rows to delete: {len(delete_ids)}")

        preview_count = max(0, int(args.limit_preview))
        if preview_count:
            print("\nPreview groups:")
            for index, (key, group) in enumerate(
                duplicate_groups[:preview_count], start=1
            ):
                keep_id = pick_keep_id(group)
                conv_id, sender_id = key
                ids = [
                    int(item.id)
                    for item in sorted(
                        group,
                        key=lambda item: (item.created_at, int(item.id)),
                        reverse=True,
                    )
                ]
                print(
                    f"{index}. conversation_id={conv_id} sender_user_id={sender_id} "
                    f"size={len(group)} keep_id={keep_id} ids={ids}"
                )

        if not args.apply:
            print("\nDry-run only. Re-run with --apply to delete duplicates.")
            return

        if not delete_ids:
            print("\nNo duplicate rows to delete.")
            return

        for message_id in delete_ids:
            row = db.get(DMMessage, message_id)
            if row:
                db.delete(row)

        db.commit()
        print(f"\nDeleted duplicate rows: {len(delete_ids)}")


if __name__ == "__main__":
    main()
