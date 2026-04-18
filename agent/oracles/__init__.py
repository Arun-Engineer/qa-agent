"""agent/oracles — Correctness oracles (Phase 4).

Four categories, in strictness order:
    universal  — always true (no 5xx, no console errors, …)
    inferred   — LLM-derived hypotheses from observed behavior
    configured — tenant-specified rules loaded from DB
    confirmed  — human-approved baselines (visual + behavioral)

A Finding is classified by the *strictest* oracle it violates.
"""
from agent.oracles.base import Finding, Oracle  # noqa: F401
from agent.oracles.universal import run_universal   # noqa: F401
from agent.oracles.inferred import run_inferred     # noqa: F401
from agent.oracles.configured import run_configured # noqa: F401
from agent.oracles.confirmed import run_confirmed   # noqa: F401
