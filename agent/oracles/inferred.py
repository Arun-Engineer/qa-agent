"""agent/oracles/inferred.py — LLM-derived behavioral hypotheses.

The crawler observes how the app behaves. From those observations, the LLM
proposes invariants the app SHOULD satisfy (e.g. "cart total equals sum of
line items", "search result count matches pagination header"), with a
confidence score.

Each hypothesis is then:
  1. Stored per-tenant with confidence (starts between 0.4 and 0.8).
  2. Tested on the next run → confidence moves up on confirm, down on
     contradict.
  3. When confidence crosses a threshold (default 0.9), the hypothesis is
     *promoted* to 'configured' so it's treated as a hard rule going forward.
  4. When confidence drops below a threshold (default 0.2), it's *retired*.

This is the "agent gets smarter over time" engine.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent.oracles.base import Finding


_STORE_PATH = Path(os.getenv("AUTO_HYPOTHESIS_STORE", "data/logs/hypotheses.json"))


@dataclass
class Hypothesis:
    id: str
    statement: str
    scope: str              # "page:/cart" | "api:/api/cart" | "global"
    confidence: float = 0.5
    observations: int = 0
    confirmations: int = 0
    contradictions: int = 0
    tenant_id: str = "default"

    def record(self, confirmed: bool) -> None:
        self.observations += 1
        if confirmed:
            self.confirmations += 1
            self.confidence = min(0.99, self.confidence + 0.1)
        else:
            self.contradictions += 1
            self.confidence = max(0.0, self.confidence - 0.2)


def _load_store() -> dict[str, Hypothesis]:
    if not _STORE_PATH.exists():
        return {}
    try:
        raw = json.loads(_STORE_PATH.read_text(encoding="utf-8"))
        return {k: Hypothesis(**v) for k, v in raw.items()}
    except Exception:
        return {}


def _save_store(store: dict[str, Hypothesis]) -> None:
    _STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _STORE_PATH.write_text(
        json.dumps({k: v.__dict__ for k, v in store.items()}, indent=2),
        encoding="utf-8",
    )


def generate_hypotheses(model, tenant_id: str = "default") -> list[Hypothesis]:
    """Ask the LLM for behavioral invariants. Returns new hypotheses only —
    dedup against the existing store by (statement, scope)."""
    if (os.getenv("AUTO_INFER_ORACLES", "1") or "1").strip().lower() in ("0", "false", "off"):
        return []
    try:
        from src.agents.langgraph_runtime import llm_json
    except Exception:
        return []

    summary = {
        "base_url": model.base_url,
        "pages": [{"url": r.url, "purpose": getattr(r, "purpose_hint", ""),
                   "xhrs": [x.fingerprint() for x in r.xhr_calls[:5]]}
                  for r in model.routes[:20]],
        "api": [x.fingerprint() for x in model.api_endpoints[:30]],
    }
    out = llm_json(
        messages=[
            {"role": "system",
             "content":
                "You are a senior QA architect. Given an application's discovered "
                "structure, propose 5-10 BEHAVIORAL INVARIANTS the app should satisfy "
                "(e.g. 'cart total == sum of line items', 'search result count matches "
                "pagination meta'). Each must be testable and scoped to a specific page "
                "or API domain.\n"
                "Return JSON: {\"hypotheses\":[{\"id\":\"h1\",\"statement\":\"...\","
                "\"scope\":\"page:/cart | api:/api/cart | global\",\"confidence\":0.6}]}"},
            {"role": "user", "content": json.dumps(summary)[:8000]},
        ],
        service="autonomous-oracle-inference",
        temperature=0.3,
    )
    raw = out.get("hypotheses") if isinstance(out, dict) else []
    existing = _load_store()
    fresh: list[Hypothesis] = []
    for item in raw[:10] if isinstance(raw, list) else []:
        if not isinstance(item, dict) or not item.get("statement"):
            continue
        key = f"{tenant_id}::{item.get('scope','global')}::{item['statement'][:80]}"
        if key in existing:
            continue
        h = Hypothesis(
            id=key,
            statement=item["statement"],
            scope=item.get("scope", "global"),
            confidence=float(item.get("confidence", 0.5)),
            tenant_id=tenant_id,
        )
        existing[key] = h
        fresh.append(h)
    _save_store(existing)
    return fresh


def run_inferred(model, *, tenant_id: str = "default") -> list[Finding]:
    """Emit findings that describe active hypotheses — the executor later
    tests them and updates confidence via `record_outcome()`."""
    # Ensure hypotheses exist (generate on first run; idempotent afterward).
    generate_hypotheses(model, tenant_id=tenant_id)
    store = _load_store()
    findings: list[Finding] = []
    for h in store.values():
        if h.tenant_id != tenant_id:
            continue
        if h.confidence < 0.2:
            continue
        findings.append(Finding(
            source=f"hypothesis:{h.id}", severity="inferred", kind="hypothesis",
            title=h.statement,
            detail=f"Scope: {h.scope}. Confidence: {h.confidence:.2f} "
                   f"({h.confirmations}/{h.observations}).",
            url=h.scope, oracle="inferred",
            confidence=h.confidence,
            evidence={"scope": h.scope, "observations": h.observations},
        ))
    return findings


def record_outcome(hypothesis_id: str, confirmed: bool) -> None:
    store = _load_store()
    h = store.get(hypothesis_id)
    if not h:
        return
    h.record(confirmed)
    # Promote / retire based on confidence thresholds.
    if h.confidence >= 0.9 and h.observations >= 5:
        # Promotion is signaled by writing to configured.py store; see there.
        try:
            from agent.oracles.configured import promote_hypothesis
            promote_hypothesis(h)
        except Exception:
            pass
    _save_store(store)
