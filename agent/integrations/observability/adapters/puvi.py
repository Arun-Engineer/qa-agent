"""Puvi Labs adapter."""
from dataclasses import dataclass, field

from agent.integrations.observability.base import BaseAdapter, FieldMap
from agent.integrations.observability import registry


@dataclass
class PuviAdapter(BaseAdapter):
    name: str = "puvi"
    display_name: str = "Puvi Labs"
    signal_keywords: list[str] = field(default_factory=lambda: [
        "puvi", "agent registry", "agent observability",
        "behavioral intelligence", "agent metrics",
    ])
    url_patterns: list[str] = field(default_factory=lambda: [
        "puvi.ai", "puvilabs.com", "puvi-labs",
    ])
    # Puvi's documented ingest shape as of now — adjust if their contract
    # changes (and the platform_profiles memory will catch that drift).
    ingest_paths: list[str] = field(default_factory=lambda: [
        "/api/v1/traces", "/v1/traces", "/ingest/traces",
    ])
    dashboard_tpl: str = "/agents/{agent_id}"


registry.register(PuviAdapter())
