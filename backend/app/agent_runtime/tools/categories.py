from __future__ import annotations

from langchain_core.tools import tool

from app.agent_runtime.tools.context import AgentContext


def make_category_tools(ctx: AgentContext) -> list:
    @tool
    def list_categories() -> list[dict]:
        """List all forum categories. Returns category ID, name, and description."""
        data = ctx._request("GET", "/bot/categories")
        if isinstance(data, dict) and "error" in data:
            return [data]
        return [
            {
                "id": c["id"],
                "name": c["name"],
                "description": c.get("description"),
            }
            for c in data
        ]

    return [list_categories]
