"""Langfuse adapter (open-source LLM observability).

Langfuse separates traces (outer session) from observations (individual
spans: generations, events, tool calls). For probe purposes we model each
tool call as one trace + one generation observation so the math checks
still work.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
import uuid

from agent.integrations.observability.base import BaseAdapter, FieldMap, TracePayload
from agent.integrations.observability import registry


@dataclass
class LangfuseAdapter(BaseAdapter):
    name: str = "langfuse"
    display_name: str = "Langfuse"
    signal_keywords: list[str] = field(default_factory=lambda: [
        "langfuse", "observation", "generation", "trace",
        "prompt management",
    ])
    url_patterns: list[str] = field(default_factory=lambda: [
        "langfuse.com", "langfuse.cloud", "cloud.langfuse",
    ])
    ingest_paths: list[str] = field(default_factory=lambda: [
        "/api/public/ingestion", "/api/public/traces",
    ])
    list_paths: list[str] = field(default_factory=lambda: [
        "/api/public/traces", "/api/public/observations",
    ])
    aggregate_paths: list[str] = field(default_factory=lambda: [
        "/api/public/metrics", "/api/public/v1/metrics",
    ])
    auth_header_candidates: list[str] = field(default_factory=lambda: [
        "Authorization",  # Basic public_key:secret_key
    ])
    dashboard_tpl: str = "/project/default/traces"
    field_map: FieldMap = field(default_factory=lambda: FieldMap(
        trace_id=["id", "traceId", "trace_id"],
        model=["model", "modelName"],
        tool=["name", "tool", "toolName"],
        latency_ms=["latency", "duration", "latencyMs"],
        is_error=["level", "statusMessage", "error"],
        prompt_tokens=["promptTokens", "usage.input"],
        completion_tokens=["completionTokens", "usage.output"],
        total_count=["count", "total"],
    ))

    def shape_ingest_payload(self, tp: TracePayload,
                             workspace_id: str = "") -> dict:
        start = datetime.fromtimestamp(tp.timestamp, tz=timezone.utc).isoformat()
        end = datetime.fromtimestamp(
            tp.timestamp + tp.latency_ms / 1000.0,
            tz=timezone.utc).isoformat()
        return {
            "batch": [
                {
                    "id": uuid.uuid4().hex,
                    "type": "trace-create",
                    "timestamp": start,
                    "body": {
                        "id": tp.trace_id,
                        "name": tp.tool,
                        "sessionId": tp.session_id,
                        "userId": tp.agent_id,
                        "metadata": {"source": "aiqa_synthetic"},
                    },
                },
                {
                    "id": uuid.uuid4().hex,
                    "type": "generation-create",
                    "timestamp": start,
                    "body": {
                        "traceId": tp.trace_id,
                        "name": tp.tool,
                        "model": tp.model,
                        "startTime": start,
                        "endTime": end,
                        "input": {"session": tp.session_id},
                        "output": {"ok": not tp.is_error,
                                   "error": tp.error_kind or None},
                        "usage": {
                            "input": tp.prompt_tokens,
                            "output": tp.completion_tokens,
                            "total": tp.prompt_tokens + tp.completion_tokens,
                        },
                        "level": "ERROR" if tp.is_error else "DEFAULT",
                        "statusMessage": tp.error_kind or "OK",
                    },
                },
            ],
        }


registry.register(LangfuseAdapter())
