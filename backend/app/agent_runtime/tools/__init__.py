from __future__ import annotations

from app.agent_runtime.tools.categories import make_category_tools
from app.agent_runtime.tools.comments import make_comment_tools
from app.agent_runtime.tools.context import AgentContext
from app.agent_runtime.tools.likes import make_like_tools
from app.agent_runtime.tools.predictions import make_prediction_tools
from app.agent_runtime.tools.threads import make_thread_tools
from app.agent_runtime.tools.users import make_user_tools
from app.agent_runtime.tools.web_browse import make_web_browse_tools
from app.agent_runtime.tools.web_search import make_web_search_tools


def build_all_tools(ctx: AgentContext, action_params: dict | None = None) -> list:
    """Build all forum tools with the given agent context bound via closures.

    When *action_params* contains ``"web_tools": True``, the web_search and
    browse_url tools are included; otherwise only the core forum tools are
    returned.
    """
    tools: list = []
    tools.extend(make_category_tools(ctx))
    tools.extend(make_thread_tools(ctx))
    tools.extend(make_comment_tools(ctx))
    tools.extend(make_like_tools(ctx))
    tools.extend(make_user_tools(ctx))
    tools.extend(make_prediction_tools(ctx))
    if action_params and action_params.get("web_tools"):
        tools.extend(make_web_search_tools(ctx))
        tools.extend(make_web_browse_tools(ctx))
    return tools
