"""LLM Provider abstraction — OpenAI + Anthropic unified interface."""
from src.llm.provider import get_llm, get_llm_for_session, detect_available_providers, get_default_provider
from src.llm.compat import chat_completion

__all__ = ["get_llm", "get_llm_for_session", "detect_available_providers",
           "get_default_provider", "chat_completion"]
