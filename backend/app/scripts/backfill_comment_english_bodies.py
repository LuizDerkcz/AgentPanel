from __future__ import annotations

import argparse
import asyncio
import json
import sys
import urllib.error
import urllib.request
import os
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.append(str(Path(__file__).resolve().parents[2]))

from openai import AsyncOpenAI
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.forum import Comment
from app.services.summarizer import detect_lang


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Translate comment bodies to English when source_lang='en' but body is not English."
        )
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=200,
        help="Max number of source_lang=en comments to scan (default: 200).",
    )
    parser.add_argument(
        "--offset",
        type=int,
        default=0,
        help="Offset when scanning comments (default: 0).",
    )
    parser.add_argument(
        "--model",
        default="",
        help="Override model name (default: OPENAI_DEFAULT_MODEL).",
    )
    parser.add_argument(
        "--comment-ids",
        default="",
        help="Comma-separated comment IDs to process exactly (overrides limit/offset scope).",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Process all source_lang=en comments (ignores limit/offset).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview updates without writing to database.",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=5,
        help="Max concurrent translation requests (default: 5).",
    )
    parser.add_argument(
        "--commit-every",
        type=int,
        default=1,
        help="Commit interval for applied updates (default: 1, commit each successful update).",
    )
    parser.add_argument(
        "--progress-every",
        type=int,
        default=10,
        help="Print progress every N completed candidates (default: 10).",
    )
    return parser.parse_args()


def should_translate(body: str) -> bool:
    if not body or not body.strip():
        return False
    return detect_lang(body) != "en"


async def translate_markdown_to_english(
    client: AsyncOpenAI,
    *,
    model: str,
    api_key: str,
    api_base: str,
    markdown_text: str,
) -> tuple[str | None, str | None]:
    prompt = (
        "Translate the following Markdown content to English. "
        "Preserve the original Markdown structure exactly, including headings, "
        "lists, links, emphasis, inline code, code fences, blockquotes, and tables. "
        "Do not add any explanation. Return only the translated Markdown content."
    )
    http_text, http_error = _translate_via_urllib(
        model=model,
        api_key=api_key,
        api_base=api_base,
        system_prompt=prompt,
        markdown_text=markdown_text,
    )
    if http_text:
        return http_text, None

    try:
        response = await client.chat.completions.create(
            model=model,
            temperature=0,
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": markdown_text},
            ],
        )
    except Exception as exc:
        return None, f"http_error={http_error}; sdk_error={exc}"

    content = response.choices[0].message.content
    if not content:
        return None, "empty response content"
    translated = content.strip()
    if not translated:
        return None, "blank translated content"
    return translated, None


def _translate_via_urllib(
    *,
    model: str,
    api_key: str,
    api_base: str,
    system_prompt: str,
    markdown_text: str,
) -> tuple[str | None, str | None]:
    endpoint = api_base.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": markdown_text},
        ],
    }
    req = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "HTTP-Referer": os.getenv("OPENROUTER_SITE_URL", "http://localhost"),
            "X-OpenRouter-Title": os.getenv(
                "OPENROUTER_SITE_NAME", "moltbook-backfill"
            ),
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = resp.read().decode("utf-8", "ignore")
    except urllib.error.HTTPError as http_err:
        try:
            raw = http_err.read().decode("utf-8", "ignore")
        except Exception:
            raw = ""
        return None, f"http_{http_err.code}: {raw[:300]}"
    except Exception as exc:
        return None, str(exc)

    try:
        data = json.loads(body)
    except Exception:
        return None, f"invalid_json: {body[:300]}"

    content = (data.get("choices") or [{}])[0].get("message", {}).get("content")
    if not content or not str(content).strip():
        return None, "empty response content"
    return str(content).strip(), None


async def main() -> None:
    args = parse_args()
    settings = get_settings()

    if not settings.openai_api_key:
        print("[ERROR] OPENAI_API_KEY is required.")
        return

    model = args.model.strip() or settings.openai_default_model
    engine = create_engine(settings.sqlalchemy_database_uri, pool_pre_ping=True)
    client = AsyncOpenAI(
        api_key=settings.openai_api_key,
        base_url=settings.openai_api_base,
    )

    scanned = 0
    candidates = 0
    translated_count = 0
    failed_count = 0
    failure_reasons: dict[str, int] = {}
    target_comment_ids = [
        int(item.strip()) for item in args.comment_ids.split(",") if item.strip()
    ]
    concurrency = max(1, int(args.concurrency or 1))
    commit_every = max(1, int(args.commit_every or 1))
    progress_every = max(1, int(args.progress_every or 1))

    with Session(engine) as db:
        query = select(Comment).where(Comment.source_lang == "en")
        if target_comment_ids:
            query = query.where(Comment.id.in_(target_comment_ids))
            query = query.order_by(Comment.id.asc())
        elif args.all:
            query = query.order_by(Comment.id.asc())
        else:
            query = (
                query.order_by(Comment.id.asc()).offset(args.offset).limit(args.limit)
            )

        comments = list(db.scalars(query).all())
        scanned = len(comments)
        comment_map = {comment.id: comment for comment in comments}
        candidates_data = [
            {"id": comment.id, "body": comment.body}
            for comment in comments
            if should_translate(comment.body)
        ]
        candidates = len(candidates_data)

        if candidates == 0:
            if args.dry_run:
                db.rollback()
            else:
                db.commit()
            mode = "DRY-RUN" if args.dry_run else "APPLIED"
            print(
                f"[{mode}] scanned={scanned}, candidates=0, translated=0, failed=0, model={model}"
            )
            return

        semaphore = asyncio.Semaphore(concurrency)

        async def run_one(candidate: dict[str, object]) -> tuple[int, str, str | None]:
            comment_id = int(candidate["id"])
            body = str(candidate["body"])
            async with semaphore:
                translated, error_message = await translate_markdown_to_english(
                    client,
                    model=model,
                    api_key=settings.openai_api_key,
                    api_base=settings.openai_api_base,
                    markdown_text=body,
                )

            if not translated:
                return comment_id, "failed", (error_message or "unknown error").strip()
            if translated == body:
                return comment_id, "unchanged", None
            return comment_id, "translated", translated

        tasks = [
            asyncio.create_task(run_one(candidate)) for candidate in candidates_data
        ]
        completed = 0
        pending_commits = 0

        for task in asyncio.as_completed(tasks):
            comment_id, status_text, payload = await task
            completed += 1

            if status_text == "failed":
                failed_count += 1
                reason = str(payload or "unknown error").strip()
                failure_reasons[reason] = failure_reasons.get(reason, 0) + 1
            elif status_text == "translated":
                comment = comment_map.get(comment_id)
                if comment is not None:
                    translated_text = str(payload)
                    comment.body = translated_text
                    comment.body_length = len(translated_text)
                    translated_count += 1
                    pending_commits += 1

                    if not args.dry_run and pending_commits >= commit_every:
                        db.commit()
                        pending_commits = 0

            if completed % progress_every == 0 or completed == candidates:
                print(
                    f"[PROGRESS] {completed}/{candidates} processed | translated={translated_count} failed={failed_count}"
                )

        if args.dry_run:
            db.rollback()
        else:
            if pending_commits > 0:
                db.commit()

    mode = "DRY-RUN" if args.dry_run else "APPLIED"
    print(
        f"[{mode}] scanned={scanned}, candidates={candidates}, "
        f"translated={translated_count}, failed={failed_count}, model={model}"
    )
    if failure_reasons:
        print("[FAILURE_REASONS]")
        sorted_reasons = sorted(
            failure_reasons.items(),
            key=lambda item: item[1],
            reverse=True,
        )
        for reason, count in sorted_reasons[:5]:
            print(f"- {count}x {reason}")


if __name__ == "__main__":
    asyncio.run(main())
