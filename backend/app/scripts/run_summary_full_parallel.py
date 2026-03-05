from __future__ import annotations

import argparse
import asyncio
import sys
from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.forum import Comment, Thread
from app.services.summarizer import (
    build_thread_overview_inputs,
    generate_answer_summary,
    generate_thread_overview_assessment,
    is_long_answer,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run full parallel summary/debate generation for threads."
    )
    parser.add_argument(
        "--thread-id",
        dest="thread_ids",
        type=int,
        nargs="+",
        default=None,
        help="Optional thread IDs to process. If omitted, all non-deleted threads are processed.",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=8,
        help="Max concurrent LLM calls.",
    )
    parser.add_argument(
        "--max-answers",
        type=int,
        default=None,
        help="Optional cap of depth=1 answers per thread (default: no limit).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Persist generated summaries to database.",
    )
    return parser.parse_args()


async def _summarize_answer(
    answer: Comment,
    semaphore: asyncio.Semaphore,
) -> tuple[int, str | None, bool]:
    body = answer.body or ""
    if not is_long_answer(body):
        return int(answer.id), "", True

    async with semaphore:
        summary = await generate_answer_summary(body)
    return int(answer.id), summary, summary is not None


async def _process_thread(
    thread: Thread,
    answers: list[Comment],
    semaphore: asyncio.Semaphore,
) -> tuple[dict, dict[int, str | None], dict]:
    answer_updates: dict[int, str | None] = {}

    answer_results = await asyncio.gather(
        *[_summarize_answer(answer, semaphore) for answer in answers],
        return_exceptions=True,
    )

    answer_ok = 0
    answer_total = len(answers)
    for result in answer_results:
        if isinstance(result, Exception):
            continue
        answer_id, summary, ok = result
        if summary is not None:
            answer_updates[answer_id] = summary
        if ok:
            answer_ok += 1

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

    async with semaphore:
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
    stats = {
        "answer_total": answer_total,
        "answer_ok": answer_ok,
        "thread_summary_ok": thread_summary is not None,
        "debate_score": debate_score,
        "debate_summary_ok": debate_summary is not None,
    }
    return thread_update, answer_updates, stats


async def main() -> None:
    args = parse_args()
    batch_size = max(1, int(args.concurrency))
    semaphore = asyncio.Semaphore(batch_size)

    db = SessionLocal()
    try:
        thread_query = select(Thread).where(Thread.status != "deleted")
        if args.thread_ids:
            thread_query = thread_query.where(Thread.id.in_(args.thread_ids))
        threads = list(db.scalars(thread_query.order_by(Thread.id.asc())).all())

        if not threads:
            print("No threads to process.")
            return

        thread_ids = [int(thread.id) for thread in threads]
        answers_query = (
            select(Comment)
            .where(
                Comment.thread_id.in_(thread_ids),
                Comment.depth == 1,
                Comment.status == "visible",
            )
            .order_by(Comment.thread_id.asc(), Comment.created_at.asc())
        )
        all_answers = list(db.scalars(answers_query).all())

        answers_by_thread: dict[int, list[Comment]] = defaultdict(list)
        for answer in all_answers:
            answers_by_thread[int(answer.thread_id)].append(answer)

        if args.max_answers is not None:
            answer_cap = max(1, int(args.max_answers))
            for thread_id in list(answers_by_thread.keys()):
                answers_by_thread[thread_id] = answers_by_thread[thread_id][:answer_cap]

        work_items: list[tuple[Thread, list[Comment]]] = []
        for thread in threads:
            answers = answers_by_thread.get(int(thread.id), [])
            if not answers:
                continue
            work_items.append((thread, answers))

        if not work_items:
            print("No eligible depth=1 visible answers found.")
            return

        print(
            f"Processing threads={len(work_items)} with concurrency={batch_size} (batch commit enabled)..."
        )

        processed_threads = 0
        thread_summary_ok = 0
        debate_summary_ok = 0

        for start in range(0, len(work_items), batch_size):
            batch = work_items[start : start + batch_size]
            results = await asyncio.gather(
                *[
                    _process_thread(thread, answers, semaphore)
                    for thread, answers in batch
                ],
                return_exceptions=True,
            )

            batch_answer_updates: dict[int, str | None] = {}
            batch_thread_updates: list[dict] = []

            for result in results:
                if isinstance(result, Exception):
                    print(f"thread task failed: {result}")
                    continue
                thread_update, answer_updates, stats = result
                processed_threads += 1
                if stats["thread_summary_ok"]:
                    thread_summary_ok += 1
                if stats["debate_summary_ok"]:
                    debate_summary_ok += 1
                batch_answer_updates.update(answer_updates)
                batch_thread_updates.append(thread_update)
                print(
                    "thread={tid}: answers={total} answer_ok={ok} "
                    "thread_summary={ts} debate_score={ds} debate_summary={db}".format(
                        tid=thread_update["thread_id"],
                        total=stats["answer_total"],
                        ok=stats["answer_ok"],
                        ts="yes" if stats["thread_summary_ok"] else "no",
                        ds=stats["debate_score"],
                        db="yes" if stats["debate_summary_ok"] else "no",
                    )
                )

            if args.apply and (batch_answer_updates or batch_thread_updates):
                if batch_answer_updates:
                    answers_to_update = list(
                        db.scalars(
                            select(Comment).where(
                                Comment.id.in_(list(batch_answer_updates.keys()))
                            )
                        ).all()
                    )
                    for answer in answers_to_update:
                        answer.answer_summary = batch_answer_updates[int(answer.id)]

                for payload in batch_thread_updates:
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
                print(
                    f"Committed batch {start // batch_size + 1}: threads={len(batch_thread_updates)}, answer_updates={len(batch_answer_updates)}"
                )

        print(
            "Summary: processed_threads={pt}, thread_summary_ok={tso}, "
            "debate_summary_ok={dso}".format(
                pt=processed_threads,
                tso=thread_summary_ok,
                dso=debate_summary_ok,
            )
        )

        if not args.apply:
            print("Dry-run finished. Re-run with --apply to persist updates.")
            return
        print("Applied updates successfully (committed per batch).")
    finally:
        db.close()


if __name__ == "__main__":
    if sys.platform.startswith("win") and hasattr(
        asyncio, "WindowsSelectorEventLoopPolicy"
    ):
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
