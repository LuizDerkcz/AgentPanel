from __future__ import annotations

import logging

from langchain_core.tools import tool

from app.agent_runtime.tools.context import AgentContext

logger = logging.getLogger(__name__)


def make_like_tools(ctx: AgentContext) -> list:
    @tool
    def like_target(target_type: str, target_id: int) -> dict:
        """Like a thread or comment. This is a one-way "like" with no dislike option.

        Note: This is separate from vote_answer. Likes affect like_count;
        to upvote/downvote a top-level answer, use vote_answer instead.

        Parameters:
        - target_type: 'thread' or 'comment'
        - target_id: the thread or comment ID
        """
        if target_type not in ("thread", "comment"):
            return {"error": "target_type must be 'thread' or 'comment'"}

        data = ctx._request(
            "POST",
            "/bot/likes",
            json={"target_type": target_type, "target_id": target_id},
        )
        if isinstance(data, dict) and "error" in data:
            # Check for "already liked" conflict
            err = data.get("error", "")
            if "Already liked" in str(err) or "already" in str(err).lower():
                return {"status": "already_liked"}
            return data

        logger.info("like_target: agent_id=%d, %s=%d", ctx.agent_id, target_type, target_id)
        return {"like_id": data.get("id"), "status": "liked"}

    @tool
    def vote_answer(comment_id: int, vote: str) -> dict:
        """Upvote or downvote a top-level answer (depth=1 comment). This is separate
        from the like system and affects upvote_count / downvote_count.

        Parameters:
        - comment_id: the answer's comment ID
        - vote: 'up', 'down', or 'cancel'
        """
        if vote not in ("up", "down", "cancel"):
            return {"error": "vote must be 'up', 'down', or 'cancel'"}

        data = ctx._request(
            "POST",
            f"/bot/comments/{comment_id}/vote",
            json={"vote": vote},
        )
        if isinstance(data, dict) and "error" in data:
            return data

        logger.info(
            "vote_answer: agent_id=%d, comment=%d, vote=%s",
            ctx.agent_id,
            comment_id,
            vote,
        )
        return {
            "comment_id": data.get("comment_id", comment_id),
            "upvote_count": data.get("upvote_count"),
            "downvote_count": data.get("downvote_count"),
            "my_vote": data.get("my_vote"),
        }

    return [like_target, vote_answer]
