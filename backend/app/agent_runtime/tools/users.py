from __future__ import annotations

from langchain_core.tools import tool

from app.agent_runtime.tools.context import AgentContext


def make_user_tools(ctx: AgentContext) -> list:
    @tool
    def get_user_info(username: str) -> dict:
        """Look up a user by username. Returns username, display name, type, avatar, etc."""
        data = ctx._request("GET", f"/bot/users/{username}")
        if isinstance(data, dict) and "error" in data:
            return data
        return {
            "id": data["id"],
            "username": data["username"],
            "display_name": data["display_name"],
            "user_type": data["user_type"],
            "avatar_url": data.get("avatar_url"),
            "is_verified": data.get("is_verified", False),
        }

    @tool
    def search_users(keyword: str, limit: int = 10) -> list[dict]:
        """Search users by username or display name.

        Parameters:
        - keyword: search term
        - limit: max results (default 10)
        """
        data = ctx._request(
            "GET",
            "/bot/users/search",
            params={"keyword": keyword, "limit": limit},
        )
        if isinstance(data, dict) and "error" in data:
            return [data]
        return [
            {
                "id": u["id"],
                "username": u["username"],
                "display_name": u["display_name"],
                "user_type": u["user_type"],
            }
            for u in data
        ]

    return [get_user_info, search_users]
