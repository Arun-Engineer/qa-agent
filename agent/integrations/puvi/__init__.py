"""agent/integrations/puvi — Puvi Labs-aware testing.

Puvi is an MLOps/agent-observability platform: companies onboard their AI
agents, the agents emit traces back to Puvi, and Puvi reports behavioral
intelligence (latency distributions, tool-use patterns, error rates,
cost breakdowns, drift signals, etc.) to customers.

A normal UI crawler can't prove this product works — the *point* of the
product is the correctness of its analytics, not the shape of its pages.
So we test Puvi with a **closed-loop probe**:

  1.  ``onboarding.run_onboarding`` — walk the signup/workspace flow,
      capture the API key or SDK token Puvi hands back.
  2.  ``synthetic_agent.SyntheticAgent`` — emits a controlled stream of
      traces with a *known* fingerprint (count, latency distribution,
      tool calls, injected errors, token usage). We own ground truth.
  3.  ``trace_roundtrip.verify_traces_roundtrip`` — polls Puvi's trace
      API (or scrapes the traces table) and confirms every emitted trace
      is accounted for, with the right shape.
  4.  ``calculations.verify_aggregates`` — recomputes averages/p95/error
      rate from what we sent and diffs against what Puvi reports.
  5.  ``consistency.verify_ui_matches_api`` — same endpoint, rendered
      via UI vs raw API, must agree.

High-level entry point: ``workflow.test_puvi_platform(base_url, …)``.
"""
from agent.integrations.puvi.synthetic_agent import (    # noqa: F401
    SyntheticAgent, TraceRecipe, GroundTruth,
)
from agent.integrations.puvi.onboarding import run_onboarding           # noqa: F401
from agent.integrations.puvi.trace_roundtrip import verify_traces_roundtrip  # noqa: F401
from agent.integrations.puvi.calculations import verify_aggregates      # noqa: F401
from agent.integrations.puvi.consistency import verify_ui_matches_api   # noqa: F401
from agent.integrations.puvi.workflow import test_puvi_platform         # noqa: F401
