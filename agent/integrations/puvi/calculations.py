"""agent/integrations/puvi/calculations.py — Verify Puvi's analytics math.

The *product* Puvi sells is behavioral intelligence: average latency, p95,
error rate, tool usage distribution, token spend per model, cost, etc.
If any of those numbers are wrong the product is broken regardless of how
nice the UI looks.

We recompute every aggregate from ground truth and compare to what Puvi
reports (via its aggregate/metrics API, or via the dashboard's data-layer
endpoint). Tolerances below reflect that Puvi legitimately may:

  * bucket latency into percentile approximations (HDR/TDigest)
  * round token counts / costs

but not to the point of drifting more than a few percent. Anything wider
than tolerance is flagged.

Endpoints are discovered with the same path-fallback approach used
elsewhere. If none of the aggregate endpoints exist, we fall back to
recomputing from the raw trace listing we already have (``listed``), which
means *if the raw data is correct, we can independently verify the UI*.
"""
from __future__ import annotations

from typing import Iterable, Optional

import requests

from agent.oracles.base import Finding
from agent.integrations.puvi.synthetic_agent import GroundTruth


_AGG_PATHS = [
    "/api/v1/metrics/agent", "/v1/metrics/agent",
    "/api/agents/{agent_id}/summary",
    "/api/v1/agents/{agent_id}/metrics",
    "/api/metrics",
]


# ── Tolerance config ──────────────────────────────────────────────────────

ABSOLUTE_COUNT_TOLERANCE = 0   # counts must match exactly
RATE_TOLERANCE = 0.02          # ±2 pp for error_rate
LATENCY_PCT_TOLERANCE = 0.08   # ±8% for avg/p50
P95_P99_PCT_TOLERANCE = 0.15   # tails are estimate-friendly
TOKEN_PCT_TOLERANCE = 0.01     # tokens should be exact, allow small rounding


def _auth_headers(api_key: str) -> dict:
    return {"Authorization": f"Bearer {api_key}", "X-Api-Key": api_key,
            "Accept": "application/json"}


def _fetch_aggregates(base_url: str, api_key: str,
                      agent_id: str) -> Optional[dict]:
    headers = _auth_headers(api_key)
    for path_tpl in _AGG_PATHS:
        path = path_tpl.format(agent_id=agent_id)
        try:
            r = requests.get(f"{base_url.rstrip('/')}{path}",
                             headers=headers,
                             params={"agent_id": agent_id}, timeout=6)
            if 200 <= r.status_code < 300:
                return r.json()
        except Exception:
            continue
    return None


def _aggregate_from_listed(listed: list[dict]) -> dict:
    """Recompute aggregates from Puvi's raw trace listing.

    Used when Puvi has no summary endpoint — lets us still verify the UI
    against the trace listing it exposes.
    """
    if not listed:
        return {}
    latencies = [x for x in (t.get("latency_ms") or t.get("duration_ms")
                             for t in listed) if isinstance(x, (int, float))]
    errors = sum(1 for t in listed
                 if t.get("is_error") or bool(t.get("error")))
    total = len(listed)
    avg = sum(latencies) / len(latencies) if latencies else 0
    s = sorted(latencies)
    n = len(s)
    def pct(p):
        return s[max(0, int(n * p) - 1)] if n else 0
    return {
        "count": total,
        "error_rate": errors / total if total else 0,
        "latency": {"avg": avg, "p50": pct(0.5),
                    "p95": pct(0.95), "p99": pct(0.99)},
    }


def _within(got, expected, pct) -> bool:
    if expected == 0:
        return abs(got) <= max(1, pct * 10)
    return abs(got - expected) / abs(expected) <= pct


def verify_aggregates(gt: GroundTruth, *, base_url: str, api_key: str,
                      listed_traces: Optional[list[dict]] = None
                      ) -> list[Finding]:
    findings: list[Finding] = []
    reported = _fetch_aggregates(base_url, api_key, gt.agent_id)
    source = "api"
    if not reported:
        # Fallback: derive from the listing — at minimum we can detect
        # disagreement between list & summary later.
        if listed_traces:
            reported = _aggregate_from_listed(listed_traces)
            source = "derived_from_listing"
        else:
            findings.append(Finding(
                source="puvi.calculations", severity="inferred", kind="ux",
                title="No aggregate/summary endpoint discoverable",
                detail="Puvi did not expose any agent metrics summary at "
                       "the conventional paths. Customers relying on the "
                       "API for dashboards will need extra work.",
                url=base_url, confidence=0.5, oracle="puvi_calculations",
            ))
            return findings

    # -- pull reported values, flexible field names ----------------------
    rep_total = (reported.get("count") or reported.get("total")
                 or reported.get("trace_count") or 0)
    rep_err_rate = (reported.get("error_rate")
                    or (reported.get("errors", 0) / rep_total
                        if rep_total else 0))
    rep_lat = (reported.get("latency")
               or reported.get("latency_ms") or {})
    if isinstance(rep_lat, (int, float)):
        rep_lat = {"avg": rep_lat}

    # -- check total count ----------------------------------------------
    accepted = getattr(gt, "delivery_stats",
                       {"accepted_by_ingest": gt.total})["accepted_by_ingest"]
    if abs(rep_total - accepted) > ABSOLUTE_COUNT_TOLERANCE:
        findings.append(Finding(
            source="puvi.calculations", severity="confirmed", kind="bug",
            title="Trace count summary disagrees with ingest",
            detail=f"Ingest ACKed {accepted} traces, summary reports "
                   f"{rep_total} ({source}).",
            url=base_url, confidence=0.95, oracle="puvi_calculations",
            evidence={"expected": accepted, "reported": rep_total,
                      "source": source},
        ))

    # -- check error rate -----------------------------------------------
    if abs(rep_err_rate - gt.error_rate) > RATE_TOLERANCE:
        findings.append(Finding(
            source="puvi.calculations", severity="confirmed", kind="bug",
            title="Error rate miscomputed",
            detail=f"Expected {gt.error_rate:.3f}, got {rep_err_rate:.3f} "
                   f"(source={source}).",
            url=base_url, confidence=0.9, oracle="puvi_calculations",
            evidence={"expected": gt.error_rate, "reported": rep_err_rate},
        ))

    # -- latency percentiles --------------------------------------------
    exp_lat = gt.latency_stats()
    for key, tol in [("avg", LATENCY_PCT_TOLERANCE),
                     ("p50", LATENCY_PCT_TOLERANCE),
                     ("p95", P95_P99_PCT_TOLERANCE),
                     ("p99", P95_P99_PCT_TOLERANCE)]:
        if key not in rep_lat or key not in exp_lat:
            continue
        if not _within(rep_lat[key], exp_lat[key], tol):
            findings.append(Finding(
                source="puvi.calculations",
                severity="universal" if key in ("avg", "p50") else "configured",
                kind="bug",
                title=f"Latency {key} outside tolerance",
                detail=f"Expected ~{exp_lat[key]:.1f}ms, "
                       f"reported {rep_lat[key]:.1f}ms "
                       f"(tolerance ±{int(tol*100)}%, source={source}).",
                url=base_url, confidence=0.85,
                oracle="puvi_calculations",
                evidence={"expected": exp_lat[key],
                          "reported": rep_lat[key]},
            ))

    # -- tool distribution (if Puvi reports it) -------------------------
    rep_tools = (reported.get("tools") or reported.get("tool_distribution")
                 or reported.get("by_tool") or {})
    if isinstance(rep_tools, list):
        # [{"tool": "x", "count": n}, ...] shape
        rep_tools = {x.get("tool") or x.get("name"): x.get("count", 0)
                     for x in rep_tools}
    if rep_tools:
        exp_tools = gt.tool_distribution()
        for tool, expected_count in exp_tools.items():
            got = rep_tools.get(tool, 0)
            if got != expected_count:
                findings.append(Finding(
                    source="puvi.calculations", severity="universal",
                    kind="bug",
                    title=f"Tool usage miscounted: {tool}",
                    detail=f"Expected {expected_count}, reported {got}.",
                    url=base_url, confidence=0.85,
                    oracle="puvi_calculations",
                    evidence={"tool": tool, "expected": expected_count,
                              "reported": got},
                ))

    # -- token totals ---------------------------------------------------
    rep_tokens = (reported.get("tokens") or reported.get("usage") or {})
    if rep_tokens:
        exp_tok = gt.token_totals()
        for k in ("total_tokens", "prompt_tokens", "completion_tokens"):
            if k not in rep_tokens:
                continue
            if not _within(rep_tokens[k], exp_tok[k], TOKEN_PCT_TOLERANCE):
                findings.append(Finding(
                    source="puvi.calculations", severity="confirmed",
                    kind="bug",
                    title=f"Token accounting wrong: {k}",
                    detail=f"Expected {exp_tok[k]}, reported {rep_tokens[k]}. "
                           f"This directly impacts billing.",
                    url=base_url, confidence=0.95,
                    oracle="puvi_calculations",
                    evidence={"field": k, "expected": exp_tok[k],
                              "reported": rep_tokens[k]},
                ))

    return findings
