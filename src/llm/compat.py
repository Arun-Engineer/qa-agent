"""
src/llm/compat.py — Backward-compatible shim.

Replaces agent.utils.openai_wrapper.chat_completion with the new provider.
Existing code that calls chat_completion(messages=..., model=..., service_name=...)
will work unchanged — it just routes through the new provider abstraction.
"""
from __future__ import annotations
import structlog
from src.llm.provider import get_llm, LLMResponse

logger = structlog.get_logger()


def chat_completion(
    messages: list[dict],
    model: str = "gpt-4o-mini",
    temperature: float = 0.2,
    service_name: str = "qa-agent",
    response_format: dict | None = None,
    **kwargs,
):
    """
    Drop-in replacement for openai_wrapper.chat_completion().
    Returns an OpenAI-style response object for backward compatibility.
    """
    # Auto-detect provider from model name
    provider = None
    if "claude" in model.lower():
        provider = "anthropic"
    elif any(x in model.lower() for x in ("gpt", "o1", "o3")):
        provider = "openai"

    try:
        llm = get_llm(provider=provider, model=model, temperature=temperature)
        resp = llm.chat(
            messages=messages,
            temperature=temperature,
            response_format=response_format,
            **kwargs,
        )

        logger.info("llm_call", service=service_name, provider=resp.provider,
                     model=resp.model, tokens=resp.usage.get("total_tokens", 0))

        # Return OpenAI-compatible wrapper so resp.choices[0].message.content works
        return _OpenAICompatResponse(resp)

    except Exception as e:
        logger.error("llm_call_failed", service=service_name, model=model, error=str(e))
        raise


class _OpenAICompatResponse:
    """Wraps LLMResponse to look like openai.ChatCompletion for backward compat."""

    def __init__(self, resp: LLMResponse):
        self._resp = resp
        self.choices = [_Choice(resp.content)]
        self.usage = type("Usage", (), resp.usage)()
        self.model = resp.model

    @property
    def content(self):
        return self._resp.content


class _Choice:
    def __init__(self, content: str):
        self.message = type("Message", (), {"content": content})()
        self.finish_reason = "stop"
