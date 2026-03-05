from __future__ import annotations

import logging

from langchain_core.tools import tool

from app.agent_runtime.tools.context import AgentContext

logger = logging.getLogger(__name__)


def make_prediction_tools(ctx: AgentContext) -> list:
    @tool
    def list_prediction_markets(limit: int = 10) -> dict:
        """List open prediction markets. Returns each market's options, vote counts,
        and whether you have already voted.

        Parameters:
        - limit: max number of markets to return (default 10)
        """
        data = ctx._request(
            "GET",
            "/bot/predictions",
            params={"status": "open", "limit": limit},
        )
        if isinstance(data, dict) and "error" in data:
            return data
        return {"markets": data, "total": len(data)}

    @tool
    def vote_prediction_market(market_id: int, option_id: int) -> dict:
        """Vote on a prediction market.

        Parameters:
        - market_id: prediction market ID
        - option_id: the option ID to vote for
        """
        data = ctx._request(
            "POST",
            f"/bot/predictions/{market_id}/vote",
            json={"option_id": option_id},
        )
        if isinstance(data, dict) and "error" in data:
            return data

        logger.info(
            "vote_prediction_market: agent_id=%d, market=%d, option=%d",
            ctx.agent_id,
            market_id,
            option_id,
        )
        # The API returns 204 No Content on success
        return {"market_id": market_id, "option_id": option_id, "status": "voted"}

    return [list_prediction_markets, vote_prediction_market]
