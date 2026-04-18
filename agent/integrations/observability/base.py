"""agent/integrations/observability/base.py — Platform-agnostic contract.

An *observability platform* (Puvi, LangSmith, Langfuse, Arize Phoenix,
Helicone, Braintrust, etc.) is any product whose core value is:

    customer's agents emit traces  →  platform stores + analyzes them
                                  →  platform reports back KPIs

Every one of those products has the same threat surface for QA:

  * signup / workspace / api-key flow — can a customer onboard?
  * trace ingest — does every emitted trace land, with the right shape?
  * analytics math — are the reported counts/percentiles/tokens correct?
  * UI consistency — do the charts match the underlying API?

Instead of writing a whole test suite per vendor, we write ONE suite and
let small ``PlatformAdapter`` modules describe how each vendor differs:

  * what keywords on the site signal "this is that vendor"
  * what JSON shape the ingest API expects
  * which auth header name the vendor honors
  * which URL paths host the trace list / aggregates / dashboard
  * how the vendor names fields in its list/aggregate responses

The generic probe in ``workflow.run_observability_probe`` consumes an
adapter + a ``TraceRecipe`` and runs the full closed-loop verification.

Adding a new vendor = ~60 lines in a new adapter file. No changes to the
oracle layer, no changes to the autonomous pipeline.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Protocol, runtime_checkable


# ── Payload shaping ──────────────────────────────────────────────────────

@dataclass
class TracePayload:
    """Neutral, internal representation of one trace event.

    Adapters transform this into whatever the target vendor's ingest
    endpoint expects. That transformation is the only place vendor-specific
    JSON shape lives.
    """
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


# ── Field-name translation ───────────────────────────────────────────────

@dataclass
class FieldMap:
    """How an adapter's response JSON names the fields we care about.

    Each value is a list of candidate keys to try in order — vendors vary
    wildly (``tool`` vs ``tool_name`` vs ``metadata.tool`` vs a tag list).
    Leaf traversal uses dotted paths, and list-contains tags are handled
    via ``resolve`` below.
    """
    trace_id:        list[str] = field(default_factory=lambda: ["trace_id", "id", "traceId"])
    model:           list[str] = field(default_factory=lambda: ["model", "model_name"])
    tool:            list[str] = field(default_factory=lambda: ["tool", "tool_name", "metadata.tool"])
    latency_ms:      list[str] = field(default_factory=lambda: ["latency_ms", "duration_ms", "latencyMs"])
    is_error:        list[str] = field(default_factory=lambda: ["is_error", "error", "status_error"])
    prompt_tokens:   list[str] = field(default_factory=lambda: ["prompt_tokens", "usage.prompt_tokens", "input_tokens"])
    completion_tokens: list[str] = field(default_factory=lambda: ["completion_tokens", "usage.completion_tokens", "output_tokens"])
    total_count:     list[str] = field(default_factory=lambda: ["count", "total", "trace_count"])
    error_rate:      list[str] = field(default_factory=lambda: ["error_rate", "errorRate"])
    latency_avg:     list[str] = field(default_factory=lambda: ["latency.avg", "avg_latency_ms", "latency_avg"])
    latency_p95:     list[str] = field(default_factory=lambda: ["latency.p95", "p95_latency_ms"])
    latency_p99:     list[str] = field(default_factory=lambda: ["latency.p99", "p99_latency_ms"])
    tool_distribution: list[str] = field(default_factory=lambda: ["tools", "tool_distribution", "by_tool"])

    @staticmethod
    def resolve(data: Any, candidates: list[str]) -> Any:
        """Try each candidate dotted path against ``data``; first hit wins."""
        for path in candidates:
            cur: Any = data
            ok = True
            for part in path.split("."):
                if isinstance(cur, dict) and part in cur:
                    cur = cur[part]
                else:
                    ok = False
                    break
            if ok:
                return cur
        return None


# ── The adapter protocol ─────────────────────────────────────────────────

@runtime_checkable
class PlatformAdapter(Protocol):
    """What every observability-platform adapter must provide.

    Implementations typically use ``@dataclass`` and set class-level
    constants rather than implement methods.
    """

    name: str                              # e.g. "puvi", "langsmith"
    display_name: str                      # shown in UI

    # Detection
    signal_keywords: list[str]             # lowercase phrases in page text
    url_patterns: list[str]                # substrings to match host

    # API contract — candidate paths, first-2xx wins at probe time
    ingest_paths: list[str]
    list_paths: list[str]
    aggregate_paths: list[str]

    # Auth convention: header names to set in priority order.
    # Value is the API key; caller picks the header key.
    auth_header_candidates: list[str]

    # URL template for the per-agent dashboard (UI consistency check).
    dashboard_tpl: str                     # e.g. "/agents/{agent_id}"

    # Field translation
    field_map: FieldMap

    # Payload shaping — convert neutral TracePayload to vendor JSON
    def shape_ingest_payload(self, tp: TracePayload,
                             workspace_id: str = "") -> dict: ...


# ── Convenience base class with sensible defaults ────────────────────────

@dataclass
class BaseAdapter:
    """Default implementation — most vendors only override a few fields."""
    name: str = "generic"
    display_name: str = "Generic observability platform"
    signal_keywords: list[str] = field(default_factory=lambda: [
        "trace", "observability", "agent", "tool call",
    ])
    url_patterns: list[str] = field(default_factory=list)
    ingest_paths: list[str] = field(default_factory=lambda: [
        "/api/v1/traces", "/v1/traces", "/ingest/traces",
        "/api/traces", "/traces",
    ])
    list_paths: list[str] = field(default_factory=lambda: [
        "/api/v1/traces", "/v1/traces", "/api/traces", "/traces",
    ])
    aggregate_paths: list[str] = field(default_factory=lambda: [
        "/api/v1/metrics/agent", "/v1/metrics/agent",
        "/api/agents/{agent_id}/summary", "/api/metrics",
    ])
    auth_header_candidates: list[str] = field(default_factory=lambda: [
        "Authorization", "X-Api-Key", "X-Auth-Token",
    ])
    dashboard_tpl: str = "/agents/{agent_id}"
    field_map: FieldMap = field(default_factory=FieldMap)

    def shape_ingest_payload(self, tp: TracePayload,
                             workspace_id: str = "") -> dict:
        """OpenTelemetry-ish default shape.

        Vendors that differ (LangSmith uses runs, Langfuse uses observations
        + generations) override this.
        """
        return {
            "trace_id": tp.trace_id,
            "agent_id": tp.agent_id,
            "session_id": tp.session_id,
            "timestamp": tp.timestamp,
            "model": tp.model,
            "tool": tp.tool,
            "latency_ms": tp.latency_ms,
            "is_error": tp.is_error,
            "error": {"kind": tp.error_kind} if tp.is_error else None,
            "usage": {
                "prompt_tokens": tp.prompt_tokens,
                "completion_tokens": tp.completion_tokens,
                "total_tokens": tp.prompt_tokens + tp.completion_tokens,
            },
            "metadata": {"source": "aiqa_synthetic",
                          "workspace": workspace_id},
        }

    def score_match(self, sample_text: str, host: str) -> float:
        """0..1 confidence that this adapter fits the target.

        Used by the registry to pick the right adapter when the user
        doesn't specify one.
        """
        t = (sample_text or "").lower()
        h = (host or "").lower()
        kw_hits = sum(1 for kw in self.signal_keywords if kw in t)
        url_hits = sum(1 for pat in self.url_patterns if pat in h)
        # URL match is much stronger evidence than keyword soup.
        return min(1.0, 0.5 * url_hits + 0.1 * kw_hits)
