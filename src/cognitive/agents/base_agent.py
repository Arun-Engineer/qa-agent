"""
src/cognitive/agents/base_agent.py — Abstract base for all cognitive agents.

Every agent: async run(context) → AgentResult
"""
from __future__ import annotations

import time, structlog
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional

from src.llm.provider import BaseLLMProvider, get_llm

logger = structlog.get_logger()


@dataclass
class AgentContext:
    """Input context passed to every agent."""
    tenant_id: str
    session_id: Optional[str] = None
    site_model: Optional[dict] = None      # Phase 2 discovery output
    spec_text: Optional[str] = None        # user spec / requirements
    target_url: Optional[str] = None
    environment: str = "SIT"
    provider: Optional[str] = None         # "openai" or "anthropic"
    model: Optional[str] = None
    extra: dict = field(default_factory=dict)


@dataclass
class AgentResult:
    """Output from any agent."""
    agent_name: str
    status: str = "ok"                     # ok | error | partial
    data: Any = None
    error: Optional[str] = None
    duration_ms: float = 0
    llm_calls: int = 0
    tokens_used: int = 0


class BaseAgent(ABC):
    """Abstract cognitive agent."""

    name: str = "base_agent"

    def __init__(self, llm: BaseLLMProvider | None = None):
        self._llm = llm
        self._call_count = 0
        self._token_count = 0

    @property
    def llm(self) -> BaseLLMProvider:
        if self._llm is None:
            self._llm = get_llm()
        return self._llm

    def _chat(self, messages: list[dict], **kw):
        """Tracked LLM call."""
        resp = self.llm.chat(messages, **kw)
        self._call_count += 1
        self._token_count += resp.usage.get("total_tokens", 0)
        return resp

    def _chat_json(self, messages: list[dict], **kw) -> dict:
        """Tracked LLM call returning JSON."""
        resp = self.llm.chat_json(messages, **kw)
        self._call_count += 1
        return resp

    async def execute(self, context: AgentContext) -> AgentResult:
        """Public entry point with timing + error handling."""
        self._call_count = 0
        self._token_count = 0

        # Override LLM if context specifies provider/model
        if context.provider or context.model:
            self._llm = get_llm(provider=context.provider, model=context.model)

        start = time.time()
        try:
            result = await self.run(context)
            result.duration_ms = round((time.time() - start) * 1000, 2)
            result.llm_calls = self._call_count
            result.tokens_used = self._token_count
            logger.info("agent_completed", agent=self.name, status=result.status,
                        duration_ms=result.duration_ms, llm_calls=self._call_count)
            return result
        except Exception as e:
            duration = round((time.time() - start) * 1000, 2)
            logger.error("agent_failed", agent=self.name, error=str(e), duration_ms=duration)
            return AgentResult(
                agent_name=self.name, status="error", error=str(e),
                duration_ms=duration, llm_calls=self._call_count,
                tokens_used=self._token_count,
            )

    @abstractmethod
    async def run(self, context: AgentContext) -> AgentResult:
        """Implement agent logic. Called by execute()."""
        ...
