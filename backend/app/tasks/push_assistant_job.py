"""
Async periodic task for DM push assistant.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from app.services.push_assistant import run_push_assistant_cycle

logger = logging.getLogger(__name__)

_task: asyncio.Task | None = None


def _seconds_until_start_time(start_time: str) -> float | None:
    text = (start_time or "").strip()
    if not text:
        return None

    try:
        hour_text, minute_text = text.split(":", 1)
        hour = int(hour_text)
        minute = int(minute_text)
        if hour < 0 or hour > 23 or minute < 0 or minute > 59:
            return None
    except Exception:
        return None

    now = datetime.now()
    next_run = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if next_run <= now:
        next_run = next_run + timedelta(days=1)
    return max(0.0, (next_run - now).total_seconds())


async def _periodic_push_loop(
    *,
    start_time: str | None,
    interval_minutes: int,
    user_batch_size: int,
    dedupe_hours: int,
    target_user_id: int | None = None,
    target_username: str | None = None,
    assistant_agent_id: int | None = None,
) -> None:
    logger.info(
        "=> DM push assistant scheduler started — start_time=%s, interval=%d min, batch=%d, dedupe=%d h, target_user_id=%s, target_username=%s, assistant_id=%s",
        start_time,
        interval_minutes,
        user_batch_size,
        dedupe_hours,
        target_user_id,
        target_username,
        assistant_agent_id,
    )

    if start_time:
        delay_seconds = _seconds_until_start_time(start_time)
        if delay_seconds is None:
            logger.warning(
                "! DM push assistant invalid start_time=%s (expected HH:MM), run immediately",
                start_time,
            )
        elif delay_seconds > 0:
            logger.info(
                "=> DM push assistant waiting %.0f second(s) until first run at %s",
                delay_seconds,
                start_time,
            )
            await asyncio.sleep(delay_seconds)

    while True:
        try:
            sent = await run_push_assistant_cycle(
                user_batch_size=user_batch_size,
                dedupe_hours=dedupe_hours,
                target_user_id=target_user_id,
                target_username=target_username,
                assistant_agent_id=assistant_agent_id,
            )
            logger.info("=> DM push assistant cycle done: sent=%d", sent)
        except Exception:
            logger.exception("! DM push assistant cycle failed")
        await asyncio.sleep(interval_minutes * 60)


async def start_push_assistant_scheduler(
    *,
    start_time: str | None,
    interval_minutes: int,
    user_batch_size: int,
    dedupe_hours: int,
    target_user_id: int | None = None,
    target_username: str | None = None,
    assistant_agent_id: int | None = None,
) -> None:
    global _task
    _task = asyncio.create_task(
        _periodic_push_loop(
            start_time=start_time,
            interval_minutes=interval_minutes,
            user_batch_size=user_batch_size,
            dedupe_hours=dedupe_hours,
            target_user_id=target_user_id,
            target_username=target_username,
            assistant_agent_id=assistant_agent_id,
        ),
        name="dm_push_assistant",
    )


def stop_push_assistant_scheduler() -> None:
    global _task
    if _task and not _task.done():
        _task.cancel()
        logger.info("=> DM push assistant scheduler stopped.")
