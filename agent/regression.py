"""agent/regression.py — Diff two runs, classify changes.

Given `run_a` (old) and `run_b` (new), produces:

    {
      "model_drift": {"fingerprint_changed": bool,
                      "routes_added": [...], "routes_removed": [...]},
      "findings_delta": {
          "new_regressions": [...],   # absent in A, present in B (bug/regression)
          "resolved":        [...],   # present in A, absent in B
          "still_broken":    [...],   # present in both
          "new_flakes":      [...],   # ewma above threshold
          "intentional":     [...],   # model drift + matching finding → not a bug
      },
      "summary": { ... }
    }

Flake filtering uses `run_intel.flake_score` so chronically flaky steps
don't spam the "new regressions" bucket.
"""
from __future__ import annotations

from typing import Any

from agent.memory import run_intel


_FLAKE_THRESHOLD = 0.3


def _key(f: dict) -> tuple[str, str]:
    """Stable identity for matching findings across runs."""
    return (f.get("source") or "", f.get("title") or "")


def diff_runs(run_a: dict, run_b: dict, *, tenant_id: str = "default") -> dict[str, Any]:
    findings_a = {_key(f): f for f in (run_a.get("findings") or [])}
    findings_b = {_key(f): f for f in (run_b.get("findings") or [])}

    new_regressions: list[dict] = []
    resolved: list[dict] = []
    still_broken: list[dict] = []
    new_flakes: list[dict] = []

    for k, fb in findings_b.items():
        if k not in findings_a:
            flake = run_intel.flake_score(tenant_id, str(k))
            if flake >= _FLAKE_THRESHOLD:
                new_flakes.append({**fb, "flake_score": flake})
            else:
                new_regressions.append(fb)
        else:
            still_broken.append(fb)

    for k, fa in findings_a.items():
        if k not in findings_b:
            resolved.append(fa)

    model_a = run_a.get("model") or {}
    model_b = run_b.get("model") or {}
    routes_a = {r.get("url") for r in model_a.get("routes", [])}
    routes_b = {r.get("url") for r in model_b.get("routes", [])}
    model_drift = {
        "fingerprint_changed":
            (model_a.get("fingerprint") != model_b.get("fingerprint")),
        "routes_added": sorted(routes_b - routes_a),
        "routes_removed": sorted(routes_a - routes_b),
    }

    # If a new route appeared AND a "regression" finding is on that route,
    # it's actually a NEW feature finding, not a regression.
    if model_drift["routes_added"]:
        kept: list[dict] = []
        intentional: list[dict] = []
        for f in new_regressions:
            if f.get("url") in model_drift["routes_added"]:
                intentional.append(f)
            else:
                kept.append(f)
        new_regressions = kept
    else:
        intentional = []

    return {
        "model_drift": model_drift,
        "findings_delta": {
            "new_regressions": new_regressions,
            "resolved": resolved,
            "still_broken": still_broken,
            "new_flakes": new_flakes,
            "intentional": intentional,
        },
        "summary": {
            "new_regressions": len(new_regressions),
            "resolved": len(resolved),
            "still_broken": len(still_broken),
            "new_flakes": len(new_flakes),
            "intentional_changes": len(intentional),
        },
    }
