"""src/agents/langgraph_runtime.py — Free-stack LangGraph runtime.

Design goals:
  * No SaaS lock-in. No LangSmith, no LangChain `ChatOpenAI`.
  * Reuse the project's existing provider (src.llm.provider), guardrails
    (src.guardrails), cost tracker + tracer (monitoring.*), and RAG pack.
  * Give workflows a small, stable helper API so every LangGraph node
    can call the LLM via the SAME chokepoint that spec_review / api_test use —
    which means input guard + output filter + cost tracking + tracer span
    apply automatically (see src/llm/compat.py).

Public API:
    llm_json(messages, *, service, temperature=0.2) -> dict
    llm_text(messages, *, service, temperature=0.2) -> str
    make_checkpointer() -> MemorySaver          # in-proc, free
    make_sqlite_checkpointer(path) -> SqliteSaver  # durable, free, optional
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger()


# ── LLM helpers that reuse the existing guarded chokepoint ───────────────────

def _call(messages: list[dict], service: str, temperature: float,
          response_format: dict | None = None) -> str:
    """Route through src.llm.compat.chat_completion so guardrails + cost +
    tracing apply — same chokepoint every legacy caller uses."""
    from src.llm.compat import chat_completion
    from src.agents import progress_bus

    # Surface live progress to any SSE listener attached to this run.
    # Label is the service name (e.g. "langgraph-review-completeness").
    progress_bus.emit("llm_call", service)

    model = os.getenv("LANGGRAPH_MODEL") or os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    resp = chat_completion(
        messages=messages,
        model=model,
        temperature=temperature,
        service_name=service,
        response_format=response_format,
    )
    progress_bus.emit("llm_done", service)
    return resp.choices[0].message.content or ""


def llm_text(messages: list[dict], *, service: str, temperature: float = 0.2) -> str:
    return _call(messages, service, temperature)


def llm_json(messages: list[dict], *, service: str, temperature: float = 0.2) -> dict:
    """Call LLM in JSON mode; tolerant to models that ignore response_format."""
    content = _call(messages, service, temperature, {"type": "json_object"})
    content = (content or "").strip()
    try:
        return json.loads(content)
    except Exception:
        # Strip fenced code blocks if any and retry
        if content.startswith("```"):
            content = content.strip("`")
            if content.lower().startswith("json"):
                content = content[4:].lstrip()
        try:
            return json.loads(content)
        except Exception as e:
            logger.warning("langgraph_json_parse_failed", error=str(e),
                           preview=content[:200])
            return {}


# ── Checkpointers (both free) ────────────────────────────────────────────────

def make_checkpointer():
    """Checkpointer selection driven by env:
      LANGGRAPH_CHECKPOINT=memory (default)  → ephemeral, fast
      LANGGRAPH_CHECKPOINT=sqlite            → durable, resumable across restarts

    Falls back to memory if sqlite saver is unavailable.
    """
    mode = (os.getenv("LANGGRAPH_CHECKPOINT", "memory") or "memory").strip().lower()
    if mode == "sqlite":
        return make_sqlite_checkpointer()
    from langgraph.checkpoint.memory import MemorySaver
    return MemorySaver()


def make_sqlite_checkpointer(path: str | None = None):
    """SQLite-backed checkpointer for durable run state (resume after crash)."""
    try:
        from langgraph.checkpoint.sqlite import SqliteSaver  # type: ignore
    except Exception as e:
        logger.info("sqlite_checkpointer_unavailable_fallback_memory", error=str(e))
        return make_checkpointer()
    p = Path(path or os.getenv("LANGGRAPH_CHECKPOINT_DB", "data/logs/lg_checkpoints.sqlite"))
    p.parent.mkdir(parents=True, exist_ok=True)
    return SqliteSaver.from_conn_string(str(p))
