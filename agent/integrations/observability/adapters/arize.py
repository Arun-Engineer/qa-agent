"""Arize Phoenix adapter (OpenTelemetry-native agent observability)."""
from dataclasses import dataclass, field

from agent.integrations.observability.base import BaseAdapter, FieldMap, TracePayload
from agent.integrations.observability import registry


@dataclass
class ArizeAdapter(BaseAdapter):
    name: str = "arize"
    display_name: str = "Arize Phoenix"
    signal_keywords: list[str] = field(default_factory=lambda: [
        "arize", "phoenix", "llm ops", "llm observability",
        "opentelemetry", "span", "embedding drift",
    ])
    url_patterns: list[str] = field(default_factory=lambda: [
        "arize.com", "phoenix.arize", "app.arize",
    ])
    ingest_paths: list[str] = field(default_factory=lambda: [
        "/v1/traces", "/v1/spans", "/api/v1/spans",
    ])
    list_paths: list[str] = field(default_factory=lambda: [
        "/v1/spans", "/v1/traces",
    ])
    aggregate_paths: list[str] = field(default_factory=lambda: [
        "/v1/metrics", "/api/metrics",
    ])
    auth_header_candidates: list[str] = field(default_factory=lambda: [
        "api_key", "Authorization",
    ])
    dashboard_tpl: str = "/projects/default/traces?agent={agent_id}"

    def shape_ingest_payload(self, tp: TracePayload,
                             workspace_id: str = "") -> dict:
        # Phoenix accepts OTLP-lite span shape.
        return {
            "resourceSpans": [{
                "resource": {
                    "attributes": [
                        {"key": "agent.id", "value": {"stringValue": tp.agent_id}},
                        {"key": "session.id", "value": {"stringValue": tp.session_id}},
                    ],
                },
                "scopeSpans": [{
                    "spans": [{
                        "traceId": tp.trace_id,
                        "spanId": tp.trace_id[:16],
                        "name": tp.tool,
                        "kind": 3,   # SPAN_KIND_CLIENT
                        "startTimeUnixNano": int(tp.timestamp * 1e9),
                        "endTimeUnixNano": int(
                            (tp.timestamp + tp.latency_ms / 1000.0) * 1e9),
                        "status": {"code": 2 if tp.is_error else 1,
                                    "message": tp.error_kind or ""},
                        "attributes": [
                            {"key": "llm.model_name",      "value": {"stringValue": tp.model}},
                            {"key": "llm.token_count.prompt",
                             "value": {"intValue": tp.prompt_tokens}},
                            {"key": "llm.token_count.completion",
                             "value": {"intValue": tp.completion_tokens}},
                            {"key": "tool.name",           "value": {"stringValue": tp.tool}},
                        ],
                    }],
                }],
            }],
        }


registry.register(ArizeAdapter())
