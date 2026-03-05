from __future__ import annotations

import asyncio
import argparse

from app.services.push_assistant import run_push_assistant_cycle


def _build_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run DM push assistant once")
    parser.add_argument("--user-id", type=int, default=None)
    parser.add_argument("--username", type=str, default=None)
    parser.add_argument("--assistant-id", type=int, default=None)
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--dedupe-hours", type=int, default=1)
    return parser.parse_args()


async def main() -> None:
    args = _build_args()
    sent = await run_push_assistant_cycle(
        user_batch_size=args.batch_size,
        dedupe_hours=args.dedupe_hours,
        target_user_id=args.user_id,
        target_username=args.username,
        assistant_agent_id=args.assistant_id,
    )
    print(f"push assistant sent: {sent}")


if __name__ == "__main__":
    asyncio.run(main())
