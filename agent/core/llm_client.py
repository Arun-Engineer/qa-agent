"""
agent/core/llm_client.py — Unified LLM client with retry and provider abstraction

Wraps the existing openai_wrapper.py with:
  - Retry with exponential backoff on rate limits / transient errors
  - Provider detection (openai vs anthropic)
  - Token usage tracking
  - Structured error handling
"""
from __future__ import annotations

import os
import time
from typing import Any, Dict, List, Optional

from agent.core.errors import LLMError, RetryExhaustedError


# Retryable HTTP status codes
RETRYABLE_CODES = {429, 500, 502, 503, 504}
MAX_RETRIES = 3
BASE_DELAY = 1.0  # seconds


class LLMClient:
    """
    Unified LLM client.

    Usage:
        client = LLMClient()
        response = client.chat(
            messages=[{"role": "user", "content": "Hello"}],
            temperature=0.2,
        )
        print(response.text)
        print(response.tokens_used)
    """

    def __init__(
        self,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        max_retries: int = MAX_RETRIES,
    ):
        self.provider = provider or os.getenv("LLM_PROVIDER", "openai")
        self.model = model or os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        self.max_retries = max_retries
        self._total_tokens = 0

    @property
    def total_tokens(self) -> int:
        return self._total_tokens

    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.3,
        response_format: Optional[Dict] = None,
        **kwargs,
    ) -> "LLMResponse":
        """
        Send a chat completion request with automatic retry.
        """
        last_error = None

        for attempt in range(self.max_retries + 1):
            try:
                return self._call(messages, temperature, response_format, **kwargs)

            except LLMError as e:
                last_error = e
                if attempt < self.max_retries and self._is_retryable(e):
                    delay = BASE_DELAY * (2 ** attempt)
                    time.sleep(delay)
                    continue
                raise

            except Exception as e:
                last_error = e
                if attempt < self.max_retries:
                    delay = BASE_DELAY * (2 ** attempt)
                    time.sleep(delay)
                    continue
                raise LLMError(
                    f"LLM call failed after {self.max_retries} retries: {e}",
                    provider=self.provider,
                    model=self.model,
                    cause=e,
                )

        raise RetryExhaustedError(
            f"LLM exhausted {self.max_retries} retries",
            cause=last_error,
        )

    def chat_json(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.2,
        **kwargs,
    ) -> Dict[str, Any]:
        """Chat with JSON response format, auto-parse result."""
        import json

        response = self.chat(
            messages=messages,
            temperature=temperature,
            response_format={"type": "json_object"},
            **kwargs,
        )

        try:
            return json.loads(response.text)
        except json.JSONDecodeError as e:
            raise LLMError(
                f"LLM returned invalid JSON: {response.text[:200]}",
                provider=self.provider,
                model=self.model,
                cause=e,
            )

    def _call(
        self,
        messages: List[Dict[str, str]],
        temperature: float,
        response_format: Optional[Dict],
        **kwargs,
    ) -> "LLMResponse":
        """Actual API call — delegates to the existing openai_wrapper."""
        try:
            from agent.utils.openai_wrapper import chat_completion

            call_kwargs = {
                "messages": messages,
                "model": self.model,
                "temperature": temperature,
                "service_name": kwargs.pop("service_name", "qa-agent"),
            }
            if response_format:
                call_kwargs["response_format"] = response_format

            resp = chat_completion(**call_kwargs)

            text = (resp.choices[0].message.content or "").strip()
            tokens = 0
            if hasattr(resp, "usage") and resp.usage:
                tokens = getattr(resp.usage, "total_tokens", 0)
            self._total_tokens += tokens

            return LLMResponse(text=text, tokens_used=tokens, raw=resp)

        except ImportError:
            raise LLMError(
                "openai_wrapper not available",
                provider=self.provider,
                model=self.model,
            )

    def _is_retryable(self, error: LLMError) -> bool:
        """Check if error is transient and worth retrying."""
        msg = str(error).lower()
        return any(
            keyword in msg
            for keyword in ["rate limit", "429", "timeout", "502", "503", "504", "overloaded"]
        )


class LLMResponse:
    """Structured LLM response."""

    def __init__(self, text: str, tokens_used: int = 0, raw: Any = None):
        self.text = text
        self.tokens_used = tokens_used
        self.raw = raw

    def __str__(self):
        return self.text
