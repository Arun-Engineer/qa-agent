"""agent/oracles/confirmed.py — Human-approved baselines.

A baseline is a point-in-time snapshot (visual or behavioral) that a human
has explicitly approved. Future runs diff against it:
  * Match  → green.
  * Drift  → finding with severity='confirmed' (highest trust).

This module is the storage/API for baselines. Phase 6 wires diffing.
"""
from __future__ import annotations

import json
import os
import hashlib
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

from agent.oracles.base import Finding


_STORE = Path(os.getenv("AUTO_BASELINES_STORE", "data/logs/baselines.json"))


@dataclass
class Baseline:
    id: str
    kind: str                   # "visual" | "dom" | "api_response"
    scope: str                  # URL or endpoint fingerprint
    hash: str                   # sha256 of the reference artifact
    artifact_path: str = ""     # filesystem path, for visual baselines
    approved_by: str = ""
    tenant_id: str = "default"
    meta: dict[str, Any] = field(default_factory=dict)


def _load() -> dict[str, Baseline]:
    if not _STORE.exists():
        return {}
    try:
        return {k: Baseline(**v) for k, v in json.loads(_STORE.read_text("utf-8")).items()}
    except Exception:
        return {}


def _save(baselines: dict[str, Baseline]) -> None:
    _STORE.parent.mkdir(parents=True, exist_ok=True)
    _STORE.write_text(
        json.dumps({k: asdict(v) for k, v in baselines.items()}, indent=2),
        encoding="utf-8",
    )


def approve(baseline: Baseline) -> None:
    """Save/replace a baseline as approved."""
    store = _load()
    store[baseline.id] = baseline
    _save(store)


def list_for_tenant(tenant_id: str) -> list[Baseline]:
    return [b for b in _load().values() if b.tenant_id == tenant_id]


def diff_against(artifact_hash: str, scope: str, *,
                 tenant_id: str = "default") -> list[Finding]:
    """Compare a freshly-captured artifact hash to the approved baseline."""
    out: list[Finding] = []
    for b in list_for_tenant(tenant_id):
        if b.scope != scope:
            continue
        if b.hash == artifact_hash:
            continue
        out.append(Finding(
            source=f"baseline:{b.id}", severity="confirmed", kind="regression",
            title=f"Baseline drift on {scope}",
            detail=f"Current artifact hash differs from approved baseline. "
                   f"baseline={b.hash[:12]} current={artifact_hash[:12]}",
            url=scope, oracle="confirmed", confidence=1.0,
            evidence={"baseline_id": b.id, "approved_by": b.approved_by},
        ))
    return out


def run_confirmed(model, *, tenant_id: str = "default") -> list[Finding]:
    """Emit a 'pending verification' finding for every baseline this tenant
    has — the executor actually does the hash compare after running."""
    findings: list[Finding] = []
    for b in list_for_tenant(tenant_id):
        findings.append(Finding(
            source=f"baseline:{b.id}", severity="confirmed", kind="hypothesis",
            title=f"Verify baseline: {b.scope}",
            detail=f"Approved {b.kind} baseline (by {b.approved_by or 'n/a'}) "
                   f"must match this run.",
            url=b.scope, oracle="confirmed", confidence=1.0,
            evidence={"baseline_id": b.id, "kind": b.kind},
        ))
    return findings


def hash_artifact(path_or_bytes) -> str:
    if isinstance(path_or_bytes, (str, Path)):
        data = Path(path_or_bytes).read_bytes()
    else:
        data = path_or_bytes
    return hashlib.sha256(data).hexdigest()
