from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timezone

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.forum import Comment, Thread
from app.services.summarizer import (
    build_thread_overview_inputs,
    generate_answer_summary,
    generate_thread_overview_assessment,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run summary/debate generation for selected threads only."
    )
    parser.add_argument(
        "--thread-id", dest="thread_ids", type=int, nargs="+", required=True
    )
    parser.add_argument(
        "--max-answers",
        type=int,
        default=None,
        help="Limit depth=1 answers per thread for quick testing. Default: no limit.",
    )
    parser.add_argument("--apply", action="store_true")
    return parser.parse_args()


async def process_thread(
    thread: Thread, answers: list[Comment]
) -> tuple[dict, dict[int, str | None]]:
    answer_updates: dict[int, str | None] = {}
    for answer in answers:
        summary = await generate_answer_summary(answer.body)
        if summary is not None:
            answer_updates[int(answer.id)] = summary

    answer_bodies = [str(answer.body or "") for answer in answers if answer.body]
    overview_inputs = build_thread_overview_inputs(
        answers,
        pending_answer_summaries=answer_updates,
    )
    previous_context = (
        thread.debate_context_snapshot
        if isinstance(thread.debate_context_snapshot, dict)
        else {}
    )
    current_context = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "answers": len(answer_bodies),
        "replies": int(thread.reply_count or 0),
        "views": int(thread.view_count or 0),
        "likes": int(thread.like_count or 0),
    }
    thread_summary, debate_summary, debate_score = (
        await generate_thread_overview_assessment(
            thread_title=thread.title,
            previous_context=previous_context,
            current_context=current_context,
            answer_bodies=overview_inputs,
            answer_summaries=overview_inputs,
        )
    )

    thread_update = {
        "thread_id": int(thread.id),
        "thread_summary": thread_summary,
        "debate_summary": debate_summary,
        "debate_score": debate_score,
        "debate_context_snapshot": current_context,
        "debate_updated_at": datetime.now(timezone.utc),
    }
    return thread_update, answer_updates


async def main() -> None:
    args = parse_args()

    db = SessionLocal()
    try:
        threads = list(
            db.scalars(
                select(Thread).where(
                    Thread.id.in_(args.thread_ids),
                    Thread.status != "deleted",
                )
            ).all()
        )
        if not threads:
            print("No matching threads.")
            return

        all_answer_updates: dict[int, str | None] = {}
        thread_updates: list[dict] = []

        for thread in threads:
            answer_query = (
                select(Comment)
                .where(
                    Comment.thread_id == thread.id,
                    Comment.depth == 1,
                    Comment.status == "visible",
                )
                .order_by(Comment.created_at.asc())
            )
            if args.max_answers is not None:
                answer_query = answer_query.limit(max(1, int(args.max_answers)))

            answers = list(db.scalars(answer_query).all())
            if not answers:
                print(f"thread={thread.id}: skipped (no depth=1 visible answers)")
                continue

            thread_update, answer_updates = await process_thread(thread, answers)
            thread_updates.append(thread_update)
            all_answer_updates.update(answer_updates)

            print(
                f"thread={thread.id}: answers={len(answers)} answer_summaries={len(answer_updates)}"
            )
            print(f"  thread_summary={thread_update['thread_summary']!r}")
            print(
                f"  debate_score={thread_update['debate_score']} debate_summary={thread_update['debate_summary']!r}"
            )

        if not args.apply:
            print("Dry-run finished. Re-run with --apply to persist updates.")
            return

        if all_answer_updates:
            answers_to_update = list(
                db.scalars(
                    select(Comment).where(
                        Comment.id.in_(list(all_answer_updates.keys()))
                    )
                ).all()
            )
            for answer in answers_to_update:
                answer.answer_summary = all_answer_updates[int(answer.id)]

        for payload in thread_updates:
            thread = db.get(Thread, payload["thread_id"])
            if not thread:
                continue
            if payload["thread_summary"] is not None:
                thread.summary = payload["thread_summary"]
            thread.debate_summary = payload["debate_summary"]
            thread.debate_score = payload["debate_score"]
            thread.debate_context_snapshot = payload["debate_context_snapshot"]
            thread.debate_updated_at = payload["debate_updated_at"]

        db.commit()
        print("Applied updates successfully.")
    finally:
        db.close()


if __name__ == "__main__":
    if sys.platform.startswith("win") and hasattr(
        asyncio, "WindowsSelectorEventLoopPolicy"
    ):
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
