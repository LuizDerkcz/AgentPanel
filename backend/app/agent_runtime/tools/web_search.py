"""Web search tool – wraps the Serper (Google) API."""

from __future__ import annotations

from typing import Any

import httpx
from langchain_core.tools import tool

from app.agent_runtime.config import SERP_API_KEY
from app.agent_runtime.tools.context import AgentContext

SERP_API_BASE = "https://google.serper.dev/search"
REQUEST_TIMEOUT = 30


def _search(query: str, num: int = 10) -> list[dict[str, Any]]:
    """Call the Serper API and return organic results.

    Args:
        query: Search query string.
        num: Number of results to request (max 100 per call).

    Returns:
        List of ``{position, title, snippet, link}`` dicts.
    """
    headers = {
        "X-API-KEY": SERP_API_KEY,
        "Content-Type": "application/json",
    }
    payload = {"q": query, "num": num}

    with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
        response = client.post(SERP_API_BASE, json=payload, headers=headers)
        response.raise_for_status()
        data = response.json()

    return [
        {
            "position": item.get("position", idx + 1),
            "title": item.get("title", ""),
            "snippet": item.get("snippet", ""),
            "link": item.get("link", ""),
        }
        for idx, item in enumerate(data.get("organic", []))
    ]


def make_web_search_tools(ctx: AgentContext) -> list:
    @tool
    def web_search(query: str, num: int = 10) -> list[dict]:
        """Search the web using Google. Returns a list of results with title, snippet, and link.

        Args:
            query: The search query string.
            num: Number of results to return (default 10, max 100).
        """
        if not SERP_API_KEY:
            return [{"error": "SERP_API_KEY not configured"}]
        try:
            return _search(query=query, num=num)
        except Exception as e:
            return [{"error": f"Search failed: {e}"}]

    return [web_search]
