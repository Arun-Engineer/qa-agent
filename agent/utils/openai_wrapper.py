"""
agent/utils/openai_wrapper.py — SHIMMED to use src.llm.provider

Original file backed up as openai_wrapper.py.bak
All existing callers (planner, chat_orchestrator, etc.) work unchanged.
"""
import os
import json
import structlog
from pathlib import Path

logger = structlog.get_logger()

# Re-export the new provider-based chat_completion
try:
    from src.llm.compat import chat_completion  # noqa: F401
except ImportError:
    # Fallback: direct OpenAI if src.llm not available
    from openai import OpenAI

    def chat_completion(messages, model="gpt-4o-mini", temperature=0.2,
                        service_name="qa-agent", response_format=None, **kwargs):
        client = OpenAI(api_key=os.getenv("OPENAI_API_KEY", ""))
        params = dict(model=model, messages=messages, temperature=temperature)
        if response_format:
            params["response_format"] = response_format
        params.update(kwargs)
        return client.chat.completions.create(**params)


def get_client(service_name: str = "qa-agent"):
    """Legacy compat — no-op now, provider is created per-call."""
    logger.info("openai_wrapper_init", service=service_name, note="using src.llm.provider")
    return None
