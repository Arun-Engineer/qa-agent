"""agent/integrations/puvi/synthetic_agent.py — Deterministic trace emitter.

The whole point of testing Puvi is that we need *ground truth* — we must
know exactly what was emitted so we can diff it against what Puvi reports.
A real agent is non-deterministic (LLM calls, variable latency, etc.) and
so useless as a probe.

The ``SyntheticAgent`` here fabricates traces according to a ``TraceRecipe``
(how many, which tools, which errors, latency distribution) and POSTs them
to Puvi's ingest endpoint using whichever SDK/HTTP shape Puvi expects.

Because Puvi's exact ingest contract varies by version, we make the HTTP
layer pluggable (``emit_fn``) but default to a reasonable "OpenTelemetry-ish
JSON over /v1/traces" shape that we try first, then fall back to a generic
``/api/traces`` POST if that 404s.

Ground truth is returned alongside — same object is consumed by
``trace_roundtrip`` and ``calculations`` to verify Puvi's numbers.
"""
from __future__ import annotations

import json
import random
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Callable, Optional, Any


# ── Recipes & ground truth ────────────────────────────────────────────────

@dataclass
class TraceRecipe:
    """Describes the *shape* of synthetic traffic to emit.

    We deliberately mix tool calls, latency tiers, and error rates so that
    Puvi's aggregate math has something non-trivial to compute. If we sent
    100 identical traces the calculation oracle couldn't catch bugs in e.g.
    the p95 computation.
    """
    total_traces: int = 50
    tools: list[str] = field(default_factory=lambda: [
        "web_search", "calculator", "sql_query", "code_exec",
    ])
    tool_weights: list[float] = field(default_factory=lambda: [
        0.4, 0.2, 0.2, 0.2,
    ])
    latency_ms_tiers: list[tuple[int, float]] = field(
        default_factory=lambda: [(150, 0.5), (800, 0.3), (2400, 0.2)]
    )   # (latency_ms, probability)
    error_rate: float = 0.1                           # fraction with is_error=True
    error_kinds: list[str] = field(default_factory=lambda: [
        "TimeoutError", "RateLimitError", "ToolError",
    ])
    model_names: list[str] = field(default_factory=lambda: [
        "gpt-4o", "claude-sonnet-4", "llama-3.1-70b",
    ])
    prompt_tokens_range: tuple[int, int] = (200, 4000)
    completion_tokens_range: tuple[int, int] = (50, 1500)
    session_count: int = 5                            # how many conversation ids
    seed: int = 42


@dataclass
class EmittedTrace:
    trace_id: str
    agent_id: str
    session_id: str
    model: str
    tool: str
    latency_ms: int
    is_error: bool
    error_kind: str
    prompt_tokens: int
    completion_tokens: int
    timestamp: float
    raw_payload: dict


@dataclass
class GroundTruth:
    """Everything we emitted. This object is the oracle's source of truth."""
    agent_id: str
    traces: list[EmittedTrace]

    @property
    def total(self) -> int:
        return len(self.traces)

    @property
    def error_count(self) -> int:
        return sum(1 for t in self.traces if t.is_error)

    @property
    def error_rate(self) -> float:
        return self.error_count / self.total if self.total else 0.0

    def latency_stats(self) -> dict:
        if not self.traces:
            return {}
        ls = sorted(t.latency_ms for t in self.traces)
        n = len(ls)
        avg = sum(ls) / n
        p50 = ls[n // 2]
        p95 = ls[max(0, int(n * 0.95) - 1)]
        p99 = ls[max(0, int(n * 0.99) - 1)]
        return {"avg": avg, "p50": p50, "p95": p95, "p99": p99,
                "min": ls[0], "max": ls[-1]}

    def tool_distribution(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for t in self.traces:
            out[t.tool] = out.get(t.tool, 0) + 1
        return out

    def model_distribution(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for t in self.traces:
            out[t.model] = out.get(t.model, 0) + 1
        return out

    def token_totals(self) -> dict[str, int]:
        return {
            "prompt_tokens": sum(t.prompt_tokens for t in self.traces),
            "completion_tokens": sum(t.completion_tokens for t in self.traces),
            "total_tokens": sum(t.prompt_tokens + t.completion_tokens
                                for t in self.traces),
        }

    def to_dict(self) -> dict:
        return {
            "agent_id": self.agent_id,
            "traces": [asdict(t) for t in self.traces],
            "summary": {
                "total": self.total,
                "error_rate": self.error_rate,
                "latency": self.latency_stats(),
                "tools": self.tool_distribution(),
                "models": self.model_distribution(),
                "tokens": self.token_totals(),
            },
        }


# ── The emitter ───────────────────────────────────────────────────────────

class SyntheticAgent:
    """Generates + emits deterministic synthetic traces.

    ``emit_fn`` takes a single trace payload (dict) and is expected to POST
    it to Puvi. If omitted we build an HTTP emitter from ``api_base`` +
    ``api_key`` that tries a few common ingest endpoints.
    """

    def __init__(self, *, agent_id: Optional[str] = None,
                 api_base: str = "", api_key: str = "",
                 emit_fn: Optional[Callable[[dict], bool]] = None):
        self.agent_id = agent_id or f"qa-agent-{uuid.uuid4().hex[:8]}"
        self.api_base = api_base.rstrip("/")
        self.api_key = api_key
        self._emit_fn = emit_fn or self._default_emit
        self._working_endpoint: Optional[str] = None

    # -- Recipe -> trace objects ------------------------------------------

    def _build_trace(self, recipe: TraceRecipe, rng: random.Random,
                     session_ids: list[str]) -> EmittedTrace:
        tool = rng.choices(recipe.tools, weights=recipe.tool_weights, k=1)[0]
        model = rng.choice(recipe.model_names)
        latencies, probs = zip(*recipe.latency_ms_tiers)
        base_latency = rng.choices(latencies, weights=probs, k=1)[0]
        # Jitter so we don't get identical buckets — otherwise p95==p50.
        latency = max(1, int(base_latency * rng.uniform(0.7, 1.4)))
        is_error = rng.random() < recipe.error_rate
        error_kind = rng.choice(recipe.error_kinds) if is_error else ""
        pt = rng.randint(*recipe.prompt_tokens_range)
        ct = rng.randint(*recipe.completion_tokens_range)
        session = rng.choice(session_ids)
        trace_id = uuid.uuid4().hex

        payload = {
            "trace_id": trace_id,
            "agent_id": self.agent_id,
            "session_id": session,
            "timestamp": time.time(),
            "model": model,
            "tool": tool,
            "latency_ms": latency,
            "is_error": is_error,
            "error": {"kind": error_kind} if is_error else None,
            "usage": {"prompt_tokens": pt, "completion_tokens": ct,
                      "total_tokens": pt + ct},
            "metadata": {"source": "aiqa_synthetic",
                          "recipe_seed": recipe.seed},
        }
        return EmittedTrace(
            trace_id=trace_id, agent_id=self.agent_id, session_id=session,
            model=model, tool=tool, latency_ms=latency, is_error=is_error,
            error_kind=error_kind, prompt_tokens=pt, completion_tokens=ct,
            timestamp=payload["timestamp"], raw_payload=payload,
        )

    # -- Emission ---------------------------------------------------------

    def emit(self, recipe: TraceRecipe) -> GroundTruth:
        rng = random.Random(recipe.seed)
        session_ids = [f"session-{uuid.uuid4().hex[:8]}"
                       for _ in range(recipe.session_count)]
        emitted: list[EmittedTrace] = []
        delivered = 0
        for _ in range(recipe.total_traces):
            t = self._build_trace(recipe, rng, session_ids)
            ok = False
            try:
                ok = bool(self._emit_fn(t.raw_payload))
            except Exception:
                ok = False
            if ok:
                delivered += 1
            emitted.append(t)
            # Small pause — avoids thundering-herd rejection on a fresh key.
            time.sleep(0.02)

        gt = GroundTruth(agent_id=self.agent_id, traces=emitted)
        # Stash delivery rate for the roundtrip oracle to interpret "missing"
        # traces correctly (difference between "never sent" and "lost").
        gt_dict = gt.to_dict()
        gt_dict["delivery"] = {"attempted": recipe.total_traces,
                                "accepted_by_ingest": delivered}
        # We don't re-wrap the dataclass; attach as attribute.
        setattr(gt, "delivery_stats", gt_dict["delivery"])
        return gt

    # -- Default HTTP emitter with endpoint discovery ---------------------

    _CANDIDATE_PATHS = [
        "/api/v1/traces", "/v1/traces", "/ingest/traces",
        "/api/traces", "/traces",
    ]

    def _default_emit(self, payload: dict) -> bool:
        import requests
        if not self.api_base:
            return False
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            # Try common header conventions; Puvi may use any of these.
            headers["Authorization"] = f"Bearer {self.api_key}"
            headers["X-Api-Key"] = self.api_key
        # Once we've found a working endpoint, stick to it.
        paths = ([self._working_endpoint] if self._working_endpoint
                 else self._CANDIDATE_PATHS)
        for path in paths:
            try:
                r = requests.post(f"{self.api_base}{path}",
                                  data=json.dumps(payload),
                                  headers=headers, timeout=5)
                if 200 <= r.status_code < 300:
                    self._working_endpoint = path
                    return True
                # 401/403 means endpoint is right but auth is wrong — stop
                # hunting so we don't spam Puvi with 401s.
                if r.status_code in (401, 403):
                    self._working_endpoint = path
                    return False
            except Exception:
                continue
        return False
