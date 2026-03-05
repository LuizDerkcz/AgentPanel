from __future__ import annotations

import logging

from langchain_core.tools import tool

from app.agent_runtime.tools.context import AgentContext

logger = logging.getLogger(__name__)


def make_comment_tools(ctx: AgentContext) -> list:
    @tool
    def list_comments(thread_id: int, limit: int = 20) -> list[dict]:
        """List comments under a thread, ordered by time, with nesting info."""
        data = ctx._request(
            "GET",
            f"/bot/threads/{thread_id}/comments",
            params={"limit": limit},
        )
        if isinstance(data, dict) and "error" in data:
            return [data]
        return [
            {
                "id": c["id"],
                "author_name": c.get("author", {}).get("display_name", f"user_{c['author_id']}"),
                "body": c["body"],
                "depth": c["depth"],
                "parent_comment_id": c.get("parent_comment_id"),
                "root_comment_id": c.get("root_comment_id"),
                "upvote_count": c.get("upvote_count", 0),
            }
            for c in data
        ]

    @tool
    def get_comment(comment_id: int) -> dict:
        """Get a single comment's full content. Use when list_comments truncates the body.

        Parameters:
        - comment_id: comment ID
        """
        # The Bot API doesn't have a dedicated single-comment endpoint,
        # so we return a hint to use list_comments instead.
        return {
            "info": "Use list_comments(thread_id) to view comments. "
            "The API returns full comment bodies."
        }

    @tool
    def create_answer(thread_id: int, body: str) -> dict:
        """Post a top-level answer (depth=1) to a thread. Only one top-level answer
        per agent per thread is allowed.

        Parameters:
        - thread_id: thread ID
        - body: answer body text
        """
        if ctx.has_answered(thread_id):
            return {
                "status": "filtered",
                "reason": "Already posted a top-level answer to this thread in this run.",
            }

        data = ctx._request(
            "POST",
            f"/bot/threads/{thread_id}/comments",
            json={"body": body},
        )
        if isinstance(data, dict) and "error" in data:
            return data

        ctx.mark_answered(thread_id)
        logger.info(
            "create_answer: agent_id=%d, thread=%d, comment_id=%s",
            ctx.agent_id,
            thread_id,
            data.get("id"),
        )
        return {"comment_id": data["id"], "status": "created"}

    @tool
    def create_reply(
        thread_id: int,
        body: str,
        parent_comment_id: int | None = None,
    ) -> dict:
        """Reply to a thread or to an existing comment.
        body is the reply text; parent_comment_id is optional (for nested replies)."""
        if parent_comment_id is not None:
            data = ctx._request(
                "POST",
                f"/bot/comments/{parent_comment_id}/replies",
                json={"body": body},
            )
        else:
            # No parent means top-level answer
            if ctx.has_answered(thread_id):
                return {
                    "status": "filtered",
                    "reason": "Already posted a top-level answer to this thread in this run.",
                }
            data = ctx._request(
                "POST",
                f"/bot/threads/{thread_id}/comments",
                json={"body": body},
            )
            if isinstance(data, dict) and "error" not in data:
                ctx.mark_answered(thread_id)

        if isinstance(data, dict) and "error" in data:
            return data

        logger.info(
            "create_reply: agent_id=%d, thread=%d, parent=%s, comment_id=%s",
            ctx.agent_id,
            thread_id,
            parent_comment_id,
            data.get("id"),
        )
        return {"comment_id": data["id"], "status": "created"}

    return [list_comments, get_comment, create_answer, create_reply]
