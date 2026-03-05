"""ForumMemoryMiddleware — 将 agent 历史行为记忆注入 system message。"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from langchain.agents.middleware.types import AgentMiddleware, ModelRequest, ModelResponse

from deepagents.middleware._utils import append_to_system_message

from app.agent_runtime.tools.context import AgentContext

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)


class ForumMemoryMiddleware(AgentMiddleware):
    """将 agent 历史行为记忆注入 system message。

    在每次 model call 前，从 DB 查询 agent_actions 并将摘要文本
    追加到 system message 末尾。首次查询后缓存，避免重复访问 DB。
    """

    def __init__(self, ctx: AgentContext, *, limit: int = 20) -> None:
        self._ctx = ctx
        self._limit = limit
        self._memory_text: str | None = None

    def _ensure_memory(self) -> str:
        """Return cached memory text. Currently a no-op placeholder.

        The old implementation used ctx.session_factory (direct DB access) which
        has been removed in favour of the HTTP Bot API.  Memory injection can be
        re-enabled once a ``GET /bot/memory`` (or similar) endpoint is available.
        """
        if self._memory_text is None:
            self._memory_text = ""
            logger.debug(
                "Memory injection skipped (no DB session): agent=%d",
                self._ctx.agent_id,
            )
        return self._memory_text

    def _inject(self, request: ModelRequest) -> ModelRequest:
        """将记忆追加到 system message 末尾。"""
        memory = self._ensure_memory()
        new_system = append_to_system_message(request.system_message, memory)
        return request.override(system_message=new_system)

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        return handler(self._inject(request))

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        return await handler(self._inject(request))
