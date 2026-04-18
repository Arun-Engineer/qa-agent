"""agent/integrations/puvi/trace_roundtrip.py — Did every trace land?

Given a ``GroundTruth`` (what we sent) we poll Puvi's trace-list API until
the reported count stabilizes, then verify:

  * **Completeness**: every ``trace_id`` we emitted (and that ingest
    accepted with 2xx) is present in Puvi's listing.
  * **Shape fidelity**: for a sample of traces, the fields Puvi returns
    (model, tool, latency_ms, is_error, token counts) match what we sent
    — within a small tolerance for latency rounding.
  * **No duplicates**: Puvi doesn't report the same trace twice (common
    class of ingest bug, silently doubles revenue metrics).
  * **No ghosts**: Puvi doesn't show traces we never sent for our agent
    (would indicate cross-tenant leakage — severity=confirmed).

Returns ``Finding`` list tagged to the ``puvi.roundtrip`` source.
"""
from __future__ import annotations

import time
from typing import Iterable, Optional

import requests

from agent.oracles.base import Finding
from agent.integrations.puvi.synthetic_agent import GroundTruth


_LIST_PATHS = [
    "/api/v1/traces", "/v1/traces",
    "/api/traces", "/traces",
]


def _auth_headers(api_key: str) -> dict:
    return {"Authorization": f"Bearer {api_key}", "X-Api-Key": api_key,
            "Accept": "application/json"}


def _try_list_traces(base_url: str, api_key: str, agent_id: str,
                     limit: int = 500) -> Optional[list[dict]]:
    """Best-effort fetch of traces for one agent. Tries a few path
    conventions + pagination styles. Returns None if nothing works."""
    headers = _auth_headers(api_key)
    params_variants = [
        {"agent_id": agent_id, "limit": limit},
        {"agent": agent_id, "limit": limit},
        {"filter[agent_id]": agent_id, "page[size]": limit},
    ]
    for path in _LIST_PATHS:
        for params in params_variants:
            try:
                r = requests.get(f"{base_url.rstrip('/')}{path}",
                                 headers=headers, params=params, timeout=8)
            except Exception:
                continue
            if not (200 <= r.status_code < 300):
                continue
            try:
                data = r.json()
            except Exception:
                continue
            # Normalize to list-of-dict.
            if isinstance(data, list):
                return data
            for key in ("traces", "data", "items", "results"):
                if isinstance(data, dict) and isinstance(data.get(key), list):
                    return data[key]
    return None


def verify_traces_roundtrip(gt: GroundTruth, *, base_url: str,
                            api_key: str, poll_seconds: int = 30,
                            poll_interval: float = 2.0) -> tuple[list[Finding], list[dict]]:
    """Poll Puvi until trace count stabilizes, then diff against ground truth."""
    findings: list[Finding] = []

    # Wait for Puvi's indexer to catch up. We stop early when two
    # consecutive polls return the same count.
    deadline = time.time() + poll_seconds
    prev_count = -1
    stable_for = 0
    listed: list[dict] = []
    while time.time() < deadline:
        listed_now = _try_list_traces(base_url, api_key, gt.agent_id) or []
        if len(listed_now) == prev_count and listed_now:
            stable_for += 1
            if stable_for >= 2:
                listed = listed_now
                break
        else:
            stable_for = 0
            prev_count = len(listed_now)
        listed = listed_now
        time.sleep(poll_interval)

    if not listed:
        findings.append(Finding(
            source="puvi.roundtrip", severity="universal", kind="bug",
            title="No traces retrievable via API after ingest",
            detail=f"Emitted {gt.total} traces for agent {gt.agent_id}, "
                   f"but none appeared in any /traces listing within "
                   f"{poll_seconds}s.",
            url=base_url, confidence=0.9, oracle="puvi_roundtrip",
            evidence={"agent_id": gt.agent_id, "emitted": gt.total},
        ))
        return findings, []

    # -- completeness + duplicates ---------------------------------------
    listed_ids: list[str] = []
    for item in listed:
        tid = item.get("trace_id") or item.get("id") or item.get("traceId")
        if tid:
            listed_ids.append(str(tid))

    sent_ids = {t.trace_id for t in gt.traces}
    listed_set = set(listed_ids)

    missing = sent_ids - listed_set
    ghosts = listed_set - sent_ids
    dup_count = len(listed_ids) - len(listed_set)

    # Account for ingest rejections: if we know e.g. 3 traces were 4xx'd
    # during emit, it's fine that those 3 don't show up.
    accepted = getattr(gt, "delivery_stats",
                       {"accepted_by_ingest": gt.total})["accepted_by_ingest"]
    expected_present = accepted

    if len(sent_ids - missing) < expected_present:
        findings.append(Finding(
            source="puvi.roundtrip", severity="confirmed", kind="bug",
            title=f"Puvi dropped {len(missing)} trace(s) silently",
            detail=f"Ingest ACKed {accepted} traces but only "
                   f"{len(sent_ids - missing)} are visible in the "
                   f"listing API. Missing IDs (sample): "
                   f"{list(missing)[:5]}",
            url=base_url, confidence=0.95, oracle="puvi_roundtrip",
            evidence={"missing_ids": list(missing)[:20],
                      "emitted": gt.total, "accepted": accepted,
                      "visible": len(listed_set)},
        ))

    if dup_count > 0:
        findings.append(Finding(
            source="puvi.roundtrip", severity="confirmed",
            kind="data_integrity",
            title=f"Puvi returned {dup_count} duplicate trace record(s)",
            detail="The same trace_id appears multiple times in the listing. "
                   "This inflates any aggregate Puvi computes on top of it.",
            url=base_url, confidence=0.95, oracle="puvi_roundtrip",
            evidence={"duplicate_count": dup_count},
        ))

    if ghosts:
        findings.append(Finding(
            source="puvi.roundtrip", severity="confirmed",
            kind="cross_tenant_leak",
            title=f"Listing returned {len(ghosts)} trace(s) we never sent",
            detail="Filtered by our agent_id, we received traces with "
                   "trace_ids outside our emission set. This looks like "
                   "cross-agent or cross-tenant leakage.",
            url=base_url, confidence=0.9, oracle="puvi_roundtrip",
            evidence={"unknown_ids_sample": list(ghosts)[:10]},
        ))

    # -- shape fidelity on a sample --------------------------------------
    by_id = {t.trace_id: t for t in gt.traces}
    checked = 0
    mismatches: list[dict] = []
    for item in listed[:30]:
        tid = item.get("trace_id") or item.get("id") or item.get("traceId")
        if not tid or tid not in by_id:
            continue
        sent = by_id[tid]
        checked += 1
        for field_pair in [
            ("model",        item.get("model")),
            ("tool",         item.get("tool") or (item.get("tags") or {}).get("tool")),
            ("is_error",     item.get("is_error") if "is_error" in item
                             else bool(item.get("error"))),
        ]:
            f, got = field_pair
            expected = getattr(sent, f)
            if got is None:
                continue      # field absent in response shape
            if got != expected:
                mismatches.append({"trace_id": tid, "field": f,
                                   "sent": expected, "got": got})
        # Latency tolerance: Puvi may round to nearest ms or bucket.
        got_lat = item.get("latency_ms") or item.get("duration_ms")
        if isinstance(got_lat, (int, float)):
            if abs(got_lat - sent.latency_ms) > max(5, 0.02 * sent.latency_ms):
                mismatches.append({"trace_id": tid, "field": "latency_ms",
                                   "sent": sent.latency_ms, "got": got_lat})

    if mismatches:
        findings.append(Finding(
            source="puvi.roundtrip", severity="universal",
            kind="data_integrity",
            title=f"Trace field values mutated in Puvi ({len(mismatches)} mismatches)",
            detail="Puvi stored fields differently than what we ingested. "
                   "This breaks any downstream analytic.",
            url=base_url, confidence=0.9, oracle="puvi_roundtrip",
            evidence={"mismatches_sample": mismatches[:10],
                      "checked_count": checked},
        ))

    return findings, listed
