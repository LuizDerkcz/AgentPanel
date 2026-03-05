from __future__ import annotations

from dataclasses import dataclass, field

import httpx


@dataclass
class AgentContext:
    api_base_url: str  # e.g. "http://localhost:8000/api/v1"
    api_key: str
    agent_user_id: int
    agent_id: int
    run_id: str
    source_lang: str = "zh"  # "zh" | "en"
    _answered_threads: set[int] = field(default_factory=set, repr=False)

    def mark_answered(self, thread_id: int) -> None:
        self._answered_threads.add(thread_id)

    def has_answered(self, thread_id: int) -> bool:
        return thread_id in self._answered_threads

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json: dict | None = None,
    ) -> dict | list:
        """Make an HTTP request to the Bot API.

        Returns parsed JSON on success, or {"error": ...} on failure.
        """
        url = f"{self.api_base_url.rstrip('/')}{path}"
        headers = {"X-Api-Key": self.api_key}
        try:
            resp = httpx.request(
                method,
                url,
                headers=headers,
                params=params,
                json=json,
                timeout=30.0,
            )
            if resp.status_code == 204:
                return {"status": "ok"}
            if resp.status_code >= 400:
                try:
                    body = resp.json()
                except Exception:
                    body = {"detail": resp.text}
                return {"error": body.get("detail", body.get("message", str(body)))}
            return resp.json()
        except httpx.HTTPError as e:
            return {"error": f"HTTP request failed: {e}"}
