"""
src/llm/provider.py — Unified LLM Provider (OpenAI + Anthropic)

Resolution priority:
  1. Explicit argument  → get_llm(provider="anthropic", model="claude-sonnet-4-20250514")
  2. User session       → set via Admin Panel /api/settings/provider
  3. config/llm.yaml    → default_provider + per-provider model
  4. .env fallback      → LLM_PROVIDER, OPENAI_API_KEY, ANTHROPIC_API_KEY
"""
from __future__ import annotations

import os, json, structlog
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional
from functools import lru_cache

logger = structlog.get_logger()

# ── Data classes ──────────────────────────────────────────────

@dataclass
class LLMResponse:
    content: str
    model: str
    provider: str
    usage: dict = field(default_factory=dict)
    raw: Any = None

    @property
    def text(self) -> str:
        return self.content


@dataclass
class LLMConfig:
    provider: str
    model: str
    api_key: str = ""
    max_tokens: int = 4096
    temperature: float = 0.2
    base_url: Optional[str] = None


# ── Abstract base ─────────────────────────────────────────────

class BaseLLMProvider(ABC):
    def __init__(self, config: LLMConfig):
        self.config = config
        self.provider_name = config.provider

    @abstractmethod
    def chat(self, messages: list[dict], model: str | None = None,
             temperature: float | None = None, max_tokens: int | None = None,
             response_format: dict | None = None, **kw) -> LLMResponse: ...

    def chat_json(self, messages: list[dict], **kw) -> dict:
        resp = self.chat(messages, response_format={"type": "json_object"}, **kw)
        text = resp.content.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1]
        if text.endswith("```"):
            text = text.rsplit("```", 1)[0]
        try:
            return json.loads(text.strip())
        except json.JSONDecodeError:
            logger.error("llm_json_parse_failed", provider=self.provider_name, raw=text[:300])
            return {"error": "Invalid JSON from LLM", "raw": text}


# ── OpenAI ────────────────────────────────────────────────────

class OpenAIProvider(BaseLLMProvider):
    def __init__(self, config: LLMConfig):
        super().__init__(config)
        from openai import OpenAI
        kw = {"base_url": config.base_url} if config.base_url else {}
        self._client = OpenAI(api_key=config.api_key, **kw)

    def chat(self, messages, model=None, temperature=None, max_tokens=None,
             response_format=None, **kw) -> LLMResponse:
        params: dict[str, Any] = dict(
            model=model or self.config.model,
            messages=messages,
            temperature=temperature if temperature is not None else self.config.temperature,
            max_tokens=max_tokens or self.config.max_tokens,
        )
        if response_format:
            params["response_format"] = response_format
        params.update(kw)
        resp = self._client.chat.completions.create(**params)
        return LLMResponse(
            content=(resp.choices[0].message.content or "").strip(),
            model=params["model"], provider="openai",
            usage={"prompt_tokens": getattr(resp.usage, "prompt_tokens", 0),
                   "completion_tokens": getattr(resp.usage, "completion_tokens", 0),
                   "total_tokens": getattr(resp.usage, "total_tokens", 0)},
            raw=resp,
        )


# ── Anthropic ─────────────────────────────────────────────────

class AnthropicProvider(BaseLLMProvider):
    def __init__(self, config: LLMConfig):
        super().__init__(config)
        import anthropic
        self._client = anthropic.Anthropic(api_key=config.api_key)

    @staticmethod
    def _split_system(messages: list[dict]) -> tuple[str, list[dict]]:
        system_parts, user_msgs = [], []
        for m in messages:
            if m.get("role") == "system":
                system_parts.append(m["content"])
            else:
                role = "assistant" if m.get("role") == "assistant" else "user"
                if user_msgs and user_msgs[-1]["role"] == role:
                    user_msgs[-1]["content"] += "\n\n" + m["content"]
                else:
                    user_msgs.append({"role": role, "content": m["content"]})
        if user_msgs and user_msgs[0]["role"] != "user":
            user_msgs.insert(0, {"role": "user", "content": "Continue."})
        return "\n\n".join(system_parts), user_msgs

    def chat(self, messages, model=None, temperature=None, max_tokens=None,
             response_format=None, **kw) -> LLMResponse:
        system, msgs = self._split_system(messages)
        params: dict[str, Any] = dict(
            model=model or self.config.model,
            messages=msgs,
            max_tokens=max_tokens or self.config.max_tokens,
        )
        if system:
            params["system"] = system
        if temperature is not None:
            params["temperature"] = temperature
        if response_format and response_format.get("type") == "json_object":
            params["system"] = (params.get("system", "") +
                "\n\nIMPORTANT: Respond ONLY with valid JSON. No markdown, no backticks.").strip()

        resp = self._client.messages.create(**params)
        content = "".join(b.text for b in resp.content if hasattr(b, "text"))
        return LLMResponse(
            content=content.strip(),
            model=params["model"], provider="anthropic",
            usage={"prompt_tokens": getattr(resp.usage, "input_tokens", 0),
                   "completion_tokens": getattr(resp.usage, "output_tokens", 0),
                   "total_tokens": getattr(resp.usage, "input_tokens", 0) +
                                   getattr(resp.usage, "output_tokens", 0)},
            raw=resp,
        )


# ── Config loading ────────────────────────────────────────────

DEFAULT_MODELS = {"openai": "gpt-4o-mini", "anthropic": "claude-sonnet-4-20250514"}
LLM_CONFIG_PATH = Path("config/llm.yaml")


@lru_cache(maxsize=1)
def _load_llm_config() -> dict:
    if LLM_CONFIG_PATH.exists():
        try:
            import yaml
            return yaml.safe_load(LLM_CONFIG_PATH.read_text(encoding="utf-8")) or {}
        except Exception:
            return {}
    return {}


def detect_available_providers() -> list[str]:
    avail = []
    if (os.getenv("OPENAI_API_KEY") or "").strip():
        avail.append("openai")
    if (os.getenv("ANTHROPIC_API_KEY") or "").strip():
        avail.append("anthropic")
    return avail


def get_default_provider() -> str:
    cfg = _load_llm_config()
    d = cfg.get("default_provider", "").strip().lower()
    if d in ("openai", "anthropic"):
        return d
    e = (os.getenv("LLM_PROVIDER") or "").strip().lower()
    if e in ("openai", "anthropic"):
        return e
    avail = detect_available_providers()
    return avail[0] if avail else "openai"


def get_llm(provider: str | None = None, model: str | None = None,
            temperature: float | None = None, max_tokens: int | None = None) -> BaseLLMProvider:
    cfg = _load_llm_config()
    p = (provider or "").strip().lower()
    if p not in ("openai", "anthropic"):
        p = get_default_provider()

    pcfg = cfg.get("providers", {}).get(p, {})
    m = model or pcfg.get("model") or DEFAULT_MODELS.get(p, "")
    t = temperature if temperature is not None else pcfg.get("temperature", 0.2)
    mx = max_tokens or pcfg.get("max_tokens", 4096)

    key = pcfg.get("api_key") or os.getenv(
        "OPENAI_API_KEY" if p == "openai" else "ANTHROPIC_API_KEY", "")
    if not key:
        raise ValueError(f"No API key for '{p}'. Set "
            f"{'OPENAI_API_KEY' if p == 'openai' else 'ANTHROPIC_API_KEY'} in .env")

    c = LLMConfig(provider=p, model=m, api_key=key, max_tokens=mx,
                  temperature=t, base_url=pcfg.get("base_url"))
    return OpenAIProvider(c) if p == "openai" else AnthropicProvider(c)


def get_llm_for_session(session: dict | None = None) -> BaseLLMProvider:
    if not session:
        return get_llm()
    active_model = (session.get("active_model") or "").strip()
    active_provider = (session.get("active_provider") or "").strip().lower()
    if not active_provider and active_model:
        if "claude" in active_model.lower():
            active_provider = "anthropic"
        elif any(x in active_model.lower() for x in ("gpt", "o1", "o3")):
            active_provider = "openai"
    return get_llm(provider=active_provider or None, model=active_model or None)
