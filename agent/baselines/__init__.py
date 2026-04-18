"""agent/baselines — Approved baselines (visual + behavioral).

Thin package that re-exports the oracle-layer Baseline machinery at a
convenient import path, plus helpers for capturing fresh artifacts.
"""
from agent.oracles.confirmed import (       # noqa: F401
    Baseline, approve, list_for_tenant, diff_against, hash_artifact,
)
from agent.baselines.visual import capture_visual_baseline  # noqa: F401
from agent.baselines.behavioral import capture_behavioral_baseline  # noqa: F401
