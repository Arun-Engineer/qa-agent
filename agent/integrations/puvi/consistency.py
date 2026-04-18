"""agent/integrations/puvi/consistency.py — UI must match the underlying API.

Puvi's dashboards are where customers actually *look* at their data. Even
if the API numbers are right, a broken chart component can quietly mislead.
This oracle:

  1. Opens the agent detail / dashboard page in a real browser.
  2. Scrapes the numbers rendered on screen (big KPI cards, chart axes,
     tables).
  3. Compares each scraped value to the API's aggregates (which we
     already verified against ground truth in ``calculations``).

Any UI number that disagrees with its own API backing is a bug — either
stale cache, wrong endpoint, or client-side math mistake.

We're deliberately permissive about how the UI renders: we look for
numeric substrings near keyword labels ("error rate", "p95", "total
traces", "tokens used") rather than depending on a specific DOM shape.
The self-healing UI runner could also be used to extract values by
semantic role; we use raw scraping here for speed.
"""
from __future__ import annotations

import re
from typing import Optional

from agent.oracles.base import Finding
from agent.integrations.puvi.synthetic_agent import GroundTruth


_NUM_RE = re.compile(r"([0-9][0-9,]*\.?[0-9]*)\s*(ms|%|k|m)?", re.I)

# Map keyword to (field-in-ground-truth, formatter, tolerance_pct)
_LABELS = [
    ("total traces",  lambda gt: gt.total,                       0.0),
    ("trace count",   lambda gt: gt.total,                       0.0),
    ("error rate",    lambda gt: gt.error_rate * 100,            0.05),
    ("errors",        lambda gt: gt.error_count,                 0.0),
    ("avg latency",   lambda gt: gt.latency_stats().get("avg", 0), 0.1),
    ("p95",           lambda gt: gt.latency_stats().get("p95", 0), 0.2),
    ("p99",           lambda gt: gt.latency_stats().get("p99", 0), 0.25),
    ("total tokens",  lambda gt: gt.token_totals()["total_tokens"], 0.02),
    ("prompt tokens", lambda gt: gt.token_totals()["prompt_tokens"], 0.02),
]


def _parse_number(raw: str) -> Optional[float]:
    m = _NUM_RE.search(raw or "")
    if not m:
        return None
    n = float(m.group(1).replace(",", ""))
    unit = (m.group(2) or "").lower()
    if unit == "k":
        n *= 1_000
    elif unit == "m":
        n *= 1_000_000
    return n


def _scrape_labelled_numbers(page, labels: list[str]) -> dict[str, float]:
    """For each label, find the closest number to it in the DOM."""
    found: dict[str, float] = {}
    try:
        text = page.inner_text("body") or ""
    except Exception:
        return found
    lower = text.lower()
    for label in labels:
        idx = lower.find(label)
        if idx < 0:
            continue
        # Look in the next 60 chars for a number — covers "Error rate: 9%"
        # and "Error rate\n9%" equally.
        window = text[idx:idx + 200]
        # skip the label itself
        tail = window[len(label):]
        val = _parse_number(tail)
        if val is not None:
            found[label] = val
    return found


def verify_ui_matches_api(browser_context, *, dashboard_url: str,
                          gt: GroundTruth) -> list[Finding]:
    findings: list[Finding] = []
    page = browser_context.new_page()
    try:
        page.goto(dashboard_url, wait_until="networkidle", timeout=20000)
    except Exception as e:
        findings.append(Finding(
            source="puvi.consistency", severity="universal", kind="bug",
            title="Agent dashboard page failed to load",
            detail=str(e), url=dashboard_url, confidence=1.0,
            oracle="puvi_consistency",
        ))
        page.close()
        return findings

    label_names = [l for l, *_ in _LABELS]
    scraped = _scrape_labelled_numbers(page, label_names)
    page.close()

    if not scraped:
        findings.append(Finding(
            source="puvi.consistency", severity="inferred", kind="ux",
            title="No KPI values scraped from dashboard",
            detail="Couldn't find numeric values next to any of the "
                   "expected labels (error rate, p95, total traces…). "
                   "Either the dashboard uses non-standard labels or it "
                   "hasn't populated yet.",
            url=dashboard_url, confidence=0.5, oracle="puvi_consistency",
            evidence={"labels_probed": label_names},
        ))
        return findings

    for label, getter, tol in _LABELS:
        if label not in scraped:
            continue
        got = scraped[label]
        try:
            expected = float(getter(gt))
        except Exception:
            continue
        # Tolerance zero => exact match required (counts).
        if tol == 0.0:
            bad = abs(got - expected) >= 1
        elif expected == 0:
            bad = abs(got) > max(1, tol * 10)
        else:
            bad = abs(got - expected) / abs(expected) > tol
        if bad:
            findings.append(Finding(
                source="puvi.consistency", severity="universal",
                kind="bug",
                title=f"Dashboard value wrong: {label}",
                detail=f"UI shows {got}, ground truth is {expected:.2f} "
                       f"(tolerance ±{int(tol*100)}%).",
                url=dashboard_url, confidence=0.85,
                oracle="puvi_consistency",
                evidence={"label": label, "ui_value": got,
                          "expected": expected},
            ))

    return findings
