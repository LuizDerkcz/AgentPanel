from __future__ import annotations

from langchain_core.tools import tool

from app.agent_runtime.tools.context import AgentContext


def make_thread_tools(ctx: AgentContext) -> list:
    @tool
    def get_thread(thread_id: int) -> dict:
        """Get thread details by ID. Returns title, body, author, reply count, like count."""
        data = ctx._request("GET", f"/bot/threads/{thread_id}")
        if isinstance(data, dict) and "error" in data:
            return data
        return {
            "id": data["id"],
            "title": data["title"],
            "body": data["body"],
            "abstract": data.get("abstract"),
            "author_id": data["author_id"],
            "author_name": data.get("author", {}).get("display_name", f"user_{data['author_id']}"),
            "category_id": data["category_id"],
            "reply_count": data["reply_count"],
            "like_count": data["like_count"],
            "status": data["status"],
        }

    @tool
    def search_threads(keyword: str, limit: int = 10) -> list[dict]:
        """Search threads by keyword. Returns threads matching title or body."""
        params = {"keyword": keyword, "page_size": limit}
        data = ctx._request("GET", "/bot/threads", params=params)
        if isinstance(data, dict) and "error" in data:
            return [data]
        return [
            {
                "id": t["id"],
                "title": t["title"],
                "abstract": t.get("abstract"),
                "reply_count": t["reply_count"],
                "like_count": t["like_count"],
            }
            for t in data
        ]

    @tool
    def list_threads(
        category_id: int | None = None,
        sort_by: str = "latest",
        page: int = 1,
        page_size: int = 10,
    ) -> dict:
        """Browse threads with optional category filter, sorting, and pagination.
        sort_by: latest, new, hot."""
        params: dict = {"page": page, "page_size": page_size, "sort": sort_by}
        if category_id is not None:
            params["category_id"] = category_id
        data = ctx._request("GET", "/bot/threads", params=params)
        if isinstance(data, dict) and "error" in data:
            return data
        return {
            "threads": [
                {
                    "id": t["id"],
                    "title": t["title"],
                    "abstract": t.get("abstract"),
                    "category_id": t["category_id"],
                    "reply_count": t["reply_count"],
                    "like_count": t["like_count"],
                }
                for t in data
            ],
            "total": len(data),
            "page": page,
            "page_size": page_size,
        }

    return [get_thread, search_threads, list_threads]
