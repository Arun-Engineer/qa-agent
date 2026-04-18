"""agent/integrations/observability — Vendor-neutral MLOps/agent-observability probe.

Exports the adapter protocol, registry, and the ``run_observability_probe``
entry point. Any site that accepts agent traces and reports analytics
(Puvi, LangSmith, Langfuse, Arize Phoenix, Helicone, Braintrust, …) can be
tested end-to-end just by selecting or auto-detecting its adapter.
"""
from agent.integrations.observability.base import (          # noqa: F401
    PlatformAdapter, BaseAdapter, TracePayload, FieldMap,
)
from agent.integrations.observability import registry         # noqa: F401
from agent.integrations.observability.probe import (          # noqa: F401
    run_observability_probe, ProbeResult, AdapterEmitter,
)
