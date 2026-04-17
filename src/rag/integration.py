"""src/rag/integration.py — Wires the RAG / guardrails / observability pack
into the running project.

This module is ADDITIVE and OPT-IN: nothing here is executed unless callers
(routes, chain engine users) explicitly import these singletons or invoke
`guarded_llm_call`. The existing orchestrator / planner paths are untouched.

Usage:
    from src.rag.integration import guarded_llm_call, get_chain_engine

    result = guarded_llm_call(messages=[...], workflow="ui_test",
                              stage="planning", tenant_id="t1")

    engine = get_chain_engine()
    chain_result = await engine.execute("ui_test", initial_context={...})
"""
from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

import structlog

from src.rag.prompts.registry import PromptRegistry
from src.rag.prompts.chain_templates import CHAIN_TEMPLATES
from src.rag.agents.prompt_chain_engine import PromptChainEngine

from src.guardrails.input_guard import InputGuard
from src.guardrails.output_filter import OutputFilter
from src.guardrails.content_filter import ContentFilter

from monitoring.tracer import Tracer
from monitoring.cost_tracker import CostTracker, CostEntry
from monitoring.feedback import FeedbackCollector
from src.evaluation.online_monitor import OnlineMonitor

logger = structlog.get_logger()


# ── Feature flag ──────────────────────────────────────────────────────────────

def rag_enabled() -> bool:
    """The pack is always importable, but callers can gate usage on this flag."""
    return (os.getenv("RAG_PACK_ENABLED", "1") or "").strip().lower() not in ("0", "false", "no", "off")


# ── Lazy singletons ───────────────────────────────────────────────────────────
# Cached with lru_cache so they are process-global and test-friendly.

@lru_cache(maxsize=1)
def get_prompt_registry() -> PromptRegistry:
    reg = PromptRegistry(load_builtins=True)
    for t in CHAIN_TEMPLATES:
        reg.register(t)
    return reg


@lru_cache(maxsize=1)
def get_tracer() -> Tracer:
    return Tracer()


@lru_cache(maxsize=1)
def get_cost_tracker() -> CostTracker:
    return CostTracker()


@lru_cache(maxsize=1)
def get_feedback_collector() -> FeedbackCollector:
    return FeedbackCollector()


@lru_cache(maxsize=1)
def get_online_monitor() -> OnlineMonitor:
    return OnlineMonitor()


@lru_cache(maxsize=1)
def get_input_guard() -> InputGuard:
    strict = (os.getenv("RAG_INPUT_GUARD_STRICT", "") or "").strip().lower() in ("1", "true", "yes", "on")
    return InputGuard(strict_mode=strict)


@lru_cache(maxsize=1)
def get_output_filter() -> OutputFilter:
    return OutputFilter()


@lru_cache(maxsize=1)
def get_content_filter() -> ContentFilter:
    return ContentFilter()


@lru_cache(maxsize=1)
def get_chain_engine() -> PromptChainEngine:
    return PromptChainEngine(
        prompt_registry=get_prompt_registry(),
        tracer=get_tracer(),
        cost_tracker=get_cost_tracker(),
        security_guards={"input": get_input_guard(), "output": get_output_filter()},
    )


def reset_singletons() -> None:
    """Clear all cached singletons. Useful in tests; not used in prod."""
    for fn in (get_prompt_registry, get_tracer, get_cost_tracker, get_feedback_collector,
               get_online_monitor, get_input_guard, get_output_filter, get_content_filter,
               get_chain_engine):
        fn.cache_clear()  # type: ignore[attr-defined]


# ── Guarded LLM call ──────────────────────────────────────────────────────────

def guarded_llm_call(
    messages: list[dict],
    *,
    workflow: str = "adhoc",
    stage: str = "",
    run_id: str = "",
    tenant_id: str = "",
    model: str | None = None,
    provider: str | None = None,
    temperature: float | None = None,
    json_mode: bool = True,
    apply_input_guard: bool = True,
    apply_output_filter: bool = True,
) -> dict[str, Any]:
    """Call the LLM with input guard + output filter + cost tracking + tracing.

    Returns a dict: {"content": str | dict, "guard": {...}, "output_check": {...},
                     "cost_usd": float, "tokens": {...}, "model": str, "provider": str}

    Does NOT raise on guard block — sets "blocked" flag instead — so callers can
    decide how to handle. Raises only on underlying LLM errors.
    """
    from src.llm.provider import get_llm  # lazy — avoids hard import order issues

    guard_result = None
    if apply_input_guard and messages:
        last = messages[-1].get("content", "")
        if isinstance(last, str):
            guard_result = get_input_guard().check(last)
            if not guard_result.is_safe:
                logger.warning("guarded_llm_blocked", threats=guard_result.threats_detected,
                               workflow=workflow, stage=stage, tenant_id=tenant_id)
                return {
                    "content": None, "blocked": True,
                    "guard": {"threat_level": guard_result.threat_level.value,
                              "threats": guard_result.threats_detected},
                    "cost_usd": 0.0, "tokens": {}, "model": model or "", "provider": provider or "",
                }
            if guard_result.sanitized_input != last:
                messages = [*messages[:-1], {**messages[-1], "content": guard_result.sanitized_input}]

    tracer = get_tracer()
    trace_id = tracer.start_trace(workflow or "llm_call", tenant_id=tenant_id)

    try:
        with tracer.span(trace_id, stage or "llm") as span:
            llm = get_llm(provider=provider, model=model)
            if json_mode:
                content = llm.chat_json(messages, temperature=temperature)
                # Try to surface usage from last response on the client if available
                usage = getattr(getattr(llm, "_last_response", None), "usage", {}) or {}
            else:
                resp = llm.chat(messages, temperature=temperature)
                content = resp.content
                usage = resp.usage

            in_tok = int(usage.get("prompt_tokens", 0) or 0)
            out_tok = int(usage.get("completion_tokens", 0) or 0)
            span.llm_calls = 1
            span.tokens_used = in_tok + out_tok

            resolved_model = getattr(llm.config, "model", model or "")
            resolved_provider = getattr(llm.config, "provider", provider or "")

            cost = CostTracker.estimate_cost(resolved_model, in_tok, out_tok)
            get_cost_tracker().record(CostEntry(
                run_id=run_id, workflow=workflow, tenant_id=tenant_id,
                model=resolved_model, input_tokens=in_tok, output_tokens=out_tok,
                cost_usd=cost, stage=stage,
            ))

            output_check = None
            if apply_output_filter:
                preview = content if isinstance(content, str) else str(content)[:4000]
                output_check = get_output_filter().check(preview)

            return {
                "content": content,
                "blocked": False,
                "guard": ({"threat_level": guard_result.threat_level.value,
                           "threats": guard_result.threats_detected}
                          if guard_result else None),
                "output_check": (
                    {"is_safe": output_check.is_safe, "issues": output_check.issues,
                     "confidence": output_check.confidence}
                    if output_check else None
                ),
                "cost_usd": cost,
                "tokens": {"input": in_tok, "output": out_tok, "total": in_tok + out_tok},
                "model": resolved_model, "provider": resolved_provider,
                "trace_id": trace_id,
            }
    finally:
        tracer.end_trace(trace_id)
