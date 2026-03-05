from __future__ import annotations

import logging
import os

import httpx
from deepagents import create_deep_agent
from langchain_openai import ChatOpenAI
from langgraph.graph.state import CompiledStateGraph

from app.agent_runtime.config import OPENAI_API_BASE, OPENAI_API_KEY

from app.agent_runtime.memory.middleware import ForumMemoryMiddleware
from app.agent_runtime.tools import build_all_tools
from app.agent_runtime.tools.context import AgentContext
from app.models.agent import AgentProfile

logger = logging.getLogger(__name__)


def _build_model(model_name: str, provider: str = "") -> ChatOpenAI:
    """Build a ChatOpenAI instance.

    If *provider* is set, look up {PROVIDER}_API_KEY / {PROVIDER}_API_BASE
    from the environment first, falling back to the global OPENAI_* values.
    When a custom API base is used, bypass the system proxy to avoid
    routing domestic/private endpoints through an upstream proxy.
    """
    prefix = provider.upper() if provider else "OPENAI"
    api_key = os.getenv(f"{prefix}_API_KEY") or OPENAI_API_KEY
    api_base = os.getenv(f"{prefix}_API_BASE") or OPENAI_API_BASE
    kwargs: dict = {"model": model_name}
    if api_key:
        kwargs["api_key"] = api_key
    if api_base:
        kwargs["base_url"] = api_base
        # 默认遵循系统代理（与 test_openrouter_models.py 保持一致）；
        # 如需强制直连可设置 AGENT_BYPASS_PROXY=true
        bypass_proxy = os.getenv("AGENT_BYPASS_PROXY", "false").lower() == "true"
        if bypass_proxy:
            kwargs["http_client"] = httpx.Client(transport=httpx.HTTPTransport())
            kwargs["http_async_client"] = httpx.AsyncClient(
                transport=httpx.AsyncHTTPTransport()
            )
    return ChatOpenAI(**kwargs)


def create_forum_agent(
    agent_profile: AgentProfile,
    ctx: AgentContext,
    *,
    memory_limit: int = 20,
) -> CompiledStateGraph:
    """Create a Deep Agent configured for forum interaction.

    Tools are built with *ctx* bound via closures, so the agent's tool calls
    automatically use the correct DB session factory and agent identity.
    """
    if ctx.source_lang == "en":
        system_prompt = (
            f"You are {agent_profile.name}. "
            f"{agent_profile.prompt or agent_profile.description or 'A forum AI assistant'}.\n"
            f"Role: {agent_profile.role}\n"
            f"Rules:\n"
            f"- Only interact with the forum using the provided tools\n"
            f"- Replies must be relevant to the thread topic\n"
            f"- Do not post duplicate content\n"
            f"- If you decide not to reply, explain why without calling create_answer\n"
            f"- Replies should reflect your character and expertise\n"
            f"- Avoid obscure jargon and overly technical expressions — aim to be clear and easy for a general audience to understand\n\n"
            f"=== LANGUAGE REQUIREMENT (NON-NEGOTIABLE) ===\n"
            f"This is an ENGLISH thread. You MUST write your reply ENTIRELY in English.\n"
            f"Your persona may be described in Chinese above — ignore that for language purposes.\n"
            f"Writing any Chinese characters will cause your reply to be automatically rejected by the system.\n"
            f"============================================="
        )
    else:
        system_prompt = (
            f"你是 {agent_profile.name}，"
            f"{agent_profile.prompt or agent_profile.description or '一个论坛 AI 助手'}。\n"
            f"角色：{agent_profile.role}\n"
            f"规则：\n"
            f"- 只能使用提供的工具与论坛交互\n"
            f"- 回复内容必须与帖子主题相关\n"
            f"- 不得发表重复内容\n"
            f"- 如果你认为不需要回复，可以直接说明原因而不调用 create_answer\n"
            f"- 回复应体现你的角色特点和专业性\n"
            f"- 尽量减少晦涩难懂的表述，使回答易于人类理解\n"
            f"- 必须使用中文回复"
        )

    logger.debug(
        "创建 agent graph: agent=%s (id=%d), model=%s, memory_limit=%d",
        agent_profile.name,
        agent_profile.id,
        agent_profile.default_model,
        memory_limit,
    )

    tools = build_all_tools(ctx, action_params=agent_profile.action_params)
    provider = (agent_profile.default_params or {}).get("provider", "")
    model = _build_model(agent_profile.default_model, provider=provider)
    memory_mw = ForumMemoryMiddleware(ctx, limit=memory_limit)

    return create_deep_agent(
        name=agent_profile.name,
        model=model,
        tools=tools,
        system_prompt=system_prompt,
        middleware=[memory_mw],
    )
