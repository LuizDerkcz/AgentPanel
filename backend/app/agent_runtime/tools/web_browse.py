"""Web browse tool – fetch and optionally summarize a URL via Jina Reader."""

from __future__ import annotations

import json
import re
import time
from typing import Any

import httpx
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI

from app.agent_runtime.config import (
    JINA_API_KEY,
    OPENAI_API_BASE,
    OPENAI_API_KEY,
    OPENAI_DEFAULT_MODEL,
)
from app.agent_runtime.tools.context import AgentContext

MAX_CONTENT_LENGTH = 50_000
REQUEST_TIMEOUT = 30
USER_AGENT = "Mozilla/5.0 (compatible; agent-browse/1.0)"

EXTRACTOR_PROMPT = """\
Please process the following webpage content and user goal to extract relevant information:

## **Webpage Content**
{webpage_content}

## **User Goal**
{goal}

## **Task Guidelines**
1. **Content Scanning for Rationale**: Locate the **specific sections/data** directly related to the user's goal within the webpage content
2. **Key Extraction for Evidence**: Identify and extract the **most relevant information** from the content, you never miss any important information, output the **full original context** of the content as far as possible, it can be more than three paragraphs.
3. **Summary Output for Summary**: Organize into a concise paragraph with logical flow, prioritizing clarity and judge the contribution of the information to the goal.

**Final Output Format using JSON format has "rational", "evidence", "summary" fields**"""


# ── helpers ─────────────────────────────────────────────────────


def _build_llm() -> ChatOpenAI:
    kwargs: dict = {"model": OPENAI_DEFAULT_MODEL, "temperature": 0}
    if OPENAI_API_KEY:
        kwargs["api_key"] = OPENAI_API_KEY
    if OPENAI_API_BASE:
        kwargs["base_url"] = OPENAI_API_BASE
    return ChatOpenAI(**kwargs)


def _extract_text_fallback(html: str) -> str:
    """Strip HTML tags with regex (no BS4 needed)."""
    html = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text)
    return "\n".join(line.strip() for line in text.split("\n") if line.strip())


def _truncate_content(content: str, max_chars: int = MAX_CONTENT_LENGTH) -> str:
    if len(content) <= max_chars:
        return content
    return content[:max_chars]


def _fetch_via_jina(url: str) -> tuple[str, dict[str, Any]]:
    """Fetch markdown content through Jina Reader API with retry + fallback."""
    jina_url = f"https://r.jina.ai/{url}"
    headers: dict[str, str] = {
        "User-Agent": USER_AGENT,
        "Accept": "text/markdown",
    }
    if JINA_API_KEY:
        headers["Authorization"] = f"Bearer {JINA_API_KEY}"

    max_retries = 3
    last_error: Exception | None = None

    for attempt in range(max_retries):
        try:
            with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
                resp = client.get(jina_url, headers=headers)
                resp.raise_for_status()
                content = _truncate_content(resp.text)
                return content, {"url": url, "via_jina": True, "length": len(content)}
        except httpx.HTTPError as exc:
            last_error = exc
            if attempt < max_retries - 1:
                time.sleep(1)

    # fallback to direct fetch
    return _fetch_direct(url)


def _fetch_direct(url: str) -> tuple[str, dict[str, Any]]:
    """Direct HTTP GET with HTML-to-text fallback."""
    try:
        with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
            resp = client.get(
                url,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                },
                follow_redirects=True,
            )
            resp.raise_for_status()
            content = _extract_text_fallback(resp.text)
            content = _truncate_content(content)
            return content, {"url": url, "length": len(content)}
    except httpx.HTTPError as exc:
        return f"Failed to fetch {url}: {exc}", {"url": url, "error": str(exc)}


def _summarize_with_llm(content: str, goal: str) -> str:
    """Use LLM to extract/summarize content based on goal."""
    prompt = EXTRACTOR_PROMPT.format(webpage_content=content, goal=goal)

    llm = _build_llm()
    max_retries = 3
    last_error: Exception | None = None

    for attempt in range(max_retries):
        try:
            response = llm.invoke(prompt)
            raw = response.content if hasattr(response, "content") else str(response)
            raw = raw.strip()

            # strip markdown code block wrapper
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]

            left = raw.find("{")
            right = raw.rfind("}")
            if left != -1 and right != -1 and right > left:
                parsed = json.loads(raw[left : right + 1])
            else:
                parsed = json.loads(raw)

            summary = parsed.get("summary", "")
            if not summary:
                summary = parsed.get("evidence", raw)
            return summary

        except (json.JSONDecodeError, Exception) as exc:
            last_error = exc

    return (
        f"The useful information for user goal '{goal}' as follows:\n\n"
        f"Evidence in page:\n"
        f"The provided webpage content could not be processed. Error: {last_error}"
    )


# ── tool factory ────────────────────────────────────────────────


def make_web_browse_tools(ctx: AgentContext) -> list:
    @tool
    def browse_url(url: str, goal: str = "", use_summary: bool = True) -> dict:
        """Fetch a web page and optionally summarize its content.

        Args:
            url: The URL to visit.
            goal: What information to extract (used for LLM summary).
            use_summary: Whether to summarize with LLM (default True). Set False to get raw content.
        """
        # normalize
        if not url.startswith(("http://", "https://")):
            url = f"https://{url}"

        content, meta = _fetch_via_jina(url)

        if "error" in meta:
            return {"error": content, "url": url}

        if use_summary and goal:
            content = _summarize_with_llm(content, goal)

        return {"url": url, "content": content}

    return [browse_url]
