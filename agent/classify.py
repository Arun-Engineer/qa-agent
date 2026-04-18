"""agent/classify.py — Map raw findings into the oracle taxonomy.

A finding arrives with a severity (universal/inferred/configured/confirmed/
noise) and a kind (bug/regression/flake/hypothesis/noise). This module
assembles the final classification report:

    {
      "total": int,
      "by_severity": {universal: n, inferred: n, configured: n, confirmed: n, noise: n},
      "by_kind":     {bug: n, regression: n, flake: n, hypothesis: n, noise: n},
      "critical":    [finding, …]   # severity ∈ {universal, confirmed} & kind == bug|regression
      "attention":   [finding, …]   # severity == configured & failure
      "signals":     [finding, …]   # severity == inferred failures → update hypothesis store
    }

It also feeds outcomes back into `oracles/inferred.py` so hypotheses earn
or lose confidence each run.
"""
from __future__ import annotations

from typing import Any

from agent.oracles.base import Finding
from agent.oracles import inferred as inferred_store


_SEVERITY_RANK = {
    "confirmed":  4,
    "universal":  3,
    "configured": 2,
    "inferred":   1,
    "noise":      0,
    "pending":    0,
}


def classify(findings: list[Finding]) -> dict[str, Any]:
    by_severity: dict[str, int] = {}
    by_kind: dict[str, int] = {}
    critical: list[Finding] = []
    attention: list[Finding] = []
    signals: list[Finding] = []

    for f in findings:
        by_severity[f.severity] = by_severity.get(f.severity, 0) + 1
        by_kind[f.kind] = by_kind.get(f.kind, 0) + 1
        if f.severity in ("universal", "confirmed") and f.kind in ("bug", "regression"):
            critical.append(f)
        elif f.severity == "configured" and f.kind in ("bug", "regression"):
            attention.append(f)
        elif f.severity == "inferred":
            signals.append(f)

    return {
        "total": len(findings),
        "by_severity": by_severity,
        "by_kind": by_kind,
        "critical": [f.to_dict() for f in critical],
        "attention": [f.to_dict() for f in attention],
        "signals": [f.to_dict() for f in signals],
    }


def record_hypothesis_outcomes(findings: list[Finding]) -> None:
    """Inspect inferred findings with a resolved `kind` and update the
    hypothesis store so next run's confidence reflects reality."""
    for f in findings:
        if f.severity != "inferred":
            continue
        # Contract: upstream sets kind='bug' when the hypothesis was proven
        # to hold (failure to satisfy → bug), or 'noise' when it was
        # contradicted (hypothesis was wrong → lower confidence).
        hid = f.evidence.get("hypothesis_id") or f.source.replace("hypothesis:", "")
        if not hid:
            continue
        confirmed = f.kind == "bug"
        inferred_store.record_outcome(hid, confirmed=confirmed)


def max_severity(findings: list[Finding]) -> str:
    top = "noise"
    rank = 0
    for f in findings:
        r = _SEVERITY_RANK.get(f.severity, 0)
        if r > rank:
            top, rank = f.severity, r
    return top
