from __future__ import annotations

import asyncio
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from app.core.config import get_settings

settings = get_settings()

# Agent-specific config
OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
OPENAI_API_BASE: str = os.getenv("OPENAI_API_BASE", "")
OPENAI_DEFAULT_MODEL: str = os.getenv("OPENAI_DEFAULT_MODEL", settings.openai_default_model)

# Web tool API keys (optional)
SERP_API_KEY: str = os.getenv("SERP_API_KEY", "")
JINA_API_KEY: str = os.getenv("JINA_API_KEY", "")

# Scheduler config
AGENT_SCHEDULER_ENABLED: bool = os.getenv("AGENT_SCHEDULER_ENABLED", "true") == "true"
AGENT_MAX_CONCURRENT: int = int(os.getenv("AGENT_MAX_CONCURRENT", "32"))

# Bot API config (for tool calls that interact with the forum API)
BOT_API_BASE_URL: str = os.getenv("BOT_API_BASE_URL", "http://localhost:8000/api/v1")
BOT_API_KEY: str = os.getenv("BOT_API_KEY", "")

_agent_semaphore = None


def get_agent_semaphore() -> asyncio.Semaphore:
    global _agent_semaphore
    if _agent_semaphore is None:
        _agent_semaphore = asyncio.Semaphore(AGENT_MAX_CONCURRENT)
    return _agent_semaphore
