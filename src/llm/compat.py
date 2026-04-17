"""
src/llm/compat.py — Backward-compatible shim.

Replaces agent.utils.openai_wrapper.chat_completion with the new provider.
Existing code that calls chat_completion(messages=..., model=..., service_name=...)
will work unchanged — it just routes through the new provider abstraction.

Phase 4 (2026-04): transparently layers the RAG pack's observability and
guardrails onto every call — input guard on user message, output filter on
response, cost tracking, and a short trace span. All layers are best-effort:
if any fail to import or execute, they degrade to a no-op so the legacy call
path is never broken.

Gate with env vars (default ON):
    QA_GUARDRAILS_ENABLED=0   → skip input/output filter
    QA_COST_TRACKING_ENABLED=0 → skip cost entry
    QA_TRACING_ENABLED=0       → skip trace span
"""
from __future__ import annotations
import os
import structlog
from src.llm.provider import get_llm, LLMResponse

logger = structlog.get_logger()


def _flag(name: str, default: bool = True) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw not in ("0", "false", "no", "off")


def _safe_import_guards():
    """Return (input_guard, output_filter) singletons or (None, None) on failure."""
    if not _flag("QA_GUARDRAILS_ENABLED"):
        return None, None
    try:
        from src.rag.integration import get_input_guard, get_output_filter
        return get_input_guard(), get_output_filter()
    except Exception as e:
        logger.debug("guardrails_unavailable", error=str(e))
        return None, None


def _safe_import_cost():
    if not _flag("QA_COST_TRACKING_ENABLED"):
        return None
    try:
        from src.rag.integration import get_cost_tracker
        return get_cost_tracker()
    except Exception as e:
        logger.debug("cost_tracker_unavailable", error=str(e))
        return None


def _safe_import_tracer():
    if not _flag("QA_TRACING_ENABLED"):
        return None
    try:
        from src.rag.integration import get_tracer
        return get_tracer()
    except Exception as e:
        logger.debug("tracer_unavailable", error=str(e))
        return None


def _apply_input_guard(messages: list[dict], service_name: str) -> list[dict]:
    """Sanitize the last user message if an InputGuard is available.

    Only blocks on HIGH / BLOCKED threat levels (hard injection, path traversal).
    LOW / MEDIUM threats (PII, truncation) return sanitized content instead.
    Block raises ValueError so existing try/except paths handle it gracefully.
    """
    guard, _ = _safe_import_guards()
    if not guard or not messages:
        return messages
    last = messages[-1]
    text = last.get("content", "")
    if not isinstance(text, str):
        return messages
    res = guard.check(text)
    if res.threats_detected:
        logger.warning("input_guard_flagged", service=service_name,
                       level=res.threat_level.value, threats=res.threats_detected)
    # Block only on HIGH or BLOCKED — don't derail LOW/MEDIUM caller flows.
    if res.threat_level.value in ("high", "blocked"):
        raise ValueError(
            f"input_guard_blocked: {res.threat_level.value} — {res.threats_detected}"
        )
    if res.sanitized_input != text:
        messages = [*messages[:-1], {**last, "content": res.sanitized_input}]
    return messages


def _apply_output_filter(content: str, service_name: str) -> None:
    _, output_filter = _safe_import_guards()
    if not output_filter or not isinstance(content, str):
        return
    res = output_filter.check(content)
    if res.issues:
        logger.warning("output_filter_flagged", service=service_name,
                       issues=res.issues, confidence=res.confidence)


def _record_cost(run_id: str, workflow: str, tenant_id: str, model: str,
                 usage: dict, stage: str) -> None:
    tracker = _safe_import_cost()
    if not tracker:
        return
    try:
        from monitoring.cost_tracker import CostEntry
        in_tok = int(usage.get("prompt_tokens", 0) or 0)
        out_tok = int(usage.get("completion_tokens", 0) or 0)
        tracker.record(CostEntry(
            run_id=run_id, workflow=workflow, tenant_id=tenant_id,
            model=model, input_tokens=in_tok, output_tokens=out_tok,
            cost_usd=0, stage=stage,
        ))
    except Exception as e:
        logger.debug("cost_record_failed", error=str(e))


def chat_completion(
    messages: list[dict],
    model: str = "gpt-4o-mini",
    temperature: float = 0.2,
    service_name: str = "qa-agent",
    response_format: dict | None = None,
    run_id: str = "",
    tenant_id: str = "",
    **kwargs,
):
    """Drop-in replacement for openai_wrapper.chat_completion().

    Accepts the historical signature. `run_id` / `tenant_id` are new optional
    kwargs that are silently swallowed by upstream wrappers — pass them when you
    have the context, the cost tracker uses them for scoped summaries.
    Returns an OpenAI-style response object for backward compatibility.
    """
    # Auto-detect provider from model name
    provider = None
    if "claude" in model.lower():
        provider = "anthropic"
    elif any(x in model.lower() for x in ("gpt", "o1", "o3")):
        provider = "openai"

    # ── Input guard (safe: only blocks HIGH/BLOCKED; sanitizes LOW/MEDIUM) ──
    try:
        messages = _apply_input_guard(messages, service_name)
    except ValueError:
        raise  # propagate block so caller's try/except sees it
    except Exception as e:
        logger.debug("input_guard_errored", error=str(e))

    tracer = _safe_import_tracer()
    trace_id = tracer.start_trace(service_name, tenant_id=tenant_id) if tracer else None

    try:
        llm = get_llm(provider=provider, model=model, temperature=temperature)
        span_ctx = tracer.span(trace_id, "llm") if tracer and trace_id else None
        span = span_ctx.__enter__() if span_ctx else None
        try:
            resp = llm.chat(
                messages=messages,
                temperature=temperature,
                response_format=response_format,
                **kwargs,
            )
            if span:
                span.llm_calls = 1
                span.tokens_used = int(resp.usage.get("total_tokens", 0) or 0)
        finally:
            if span_ctx:
                span_ctx.__exit__(None, None, None)

        logger.info("llm_call", service=service_name, provider=resp.provider,
                     model=resp.model, tokens=resp.usage.get("total_tokens", 0))

        # ── Output filter (log-only, never mutates response) ──
        _apply_output_filter(resp.content, service_name)

        # ── Cost tracking ──
        _record_cost(run_id=run_id, workflow=service_name, tenant_id=tenant_id,
                     model=resp.model, usage=resp.usage, stage="chat")

        # Return OpenAI-compatible wrapper so resp.choices[0].message.content works
        return _OpenAICompatResponse(resp)

    except Exception as e:
        logger.error("llm_call_failed", service=service_name, model=model, error=str(e))
        raise
    finally:
        if tracer and trace_id:
            tracer.end_trace(trace_id)


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
