"""LangSmith adapter (LangChain's observability product).

LangSmith uses a ``runs`` nomenclature instead of ``traces``, and its
ingest payload is substantially different (run_type, inputs/outputs,
start_time/end_time ISO strings). This adapter translates our neutral
``TracePayload`` into that shape.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone

from agent.integrations.observability.base import BaseAdapter, FieldMap, TracePayload
from agent.integrations.observability import registry


@dataclass
class LangSmithAdapter(BaseAdapter):
    name: str = "langsmith"
    display_name: str = "LangSmith (LangChain)"
    signal_keywords: list[str] = field(default_factory=lambda: [
        "langsmith", "langchain", "trace a run", "run tree",
        "evaluate", "dataset",
    ])
    url_patterns: list[str] = field(default_factory=lambda: [
        "smith.langchain.com", "langsmith", "api.smith.langchain",
    ])
    ingest_paths: list[str] = field(default_factory=lambda: [
        "/runs", "/api/v1/runs", "/api/runs",
    ])
    list_paths: list[str] = field(default_factory=lambda: [
        "/runs", "/api/v1/runs", "/api/runs/query",
    ])
    aggregate_paths: list[str] = field(default_factory=lambda: [
        "/runs/stats", "/api/v1/runs/stats",
    ])
    auth_header_candidates: list[str] = field(default_factory=lambda: [
        "X-API-Key", "Authorization",
    ])
    dashboard_tpl: str = "/o/default/projects/p/{agent_id}"
    field_map: FieldMap = field(default_factory=lambda: FieldMap(
        trace_id=["id", "run_id", "trace_id"],
        model=["serialized.model", "extra.invocation_params.model", "model"],
        tool=["serialized.tool", "extra.tool", "name"],
        latency_ms=["latency_ms", "total_time_ms"],
        is_error=["error", "status"],
        prompt_tokens=["prompt_tokens", "inputs.tokens.prompt"],
        completion_tokens=["completion_tokens", "outputs.tokens.completion"],
        total_count=["total", "count"],
    ))

    def shape_ingest_payload(self, tp: TracePayload,
                             workspace_id: str = "") -> dict:
        start = datetime.fromtimestamp(tp.timestamp, tz=timezone.utc)
        end = datetime.fromtimestamp(
            tp.timestamp + tp.latency_ms / 1000.0, tz=timezone.utc)
        return {
            "id": tp.trace_id,
            "name": tp.tool,
            "run_type": "tool",
            "start_time": start.isoformat(),
            "end_time": end.isoformat(),
            "inputs": {"session": tp.session_id, "model": tp.model},
            "outputs": {"ok": not tp.is_error},
            "error": tp.error_kind if tp.is_error else None,
            "extra": {
                "invocation_params": {"model": tp.model},
                "metadata": {"source": "aiqa_synthetic",
                             "agent_id": tp.agent_id},
            },
            "serialized": {"model": tp.model, "tool": tp.tool},
            # LangSmith accepts usage in extras on recent API versions.
            "prompt_tokens": tp.prompt_tokens,
            "completion_tokens": tp.completion_tokens,
            "total_tokens": tp.prompt_tokens + tp.completion_tokens,
            "session_name": workspace_id or tp.agent_id,
        }


registry.register(LangSmithAdapter())
