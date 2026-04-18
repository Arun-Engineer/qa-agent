"""agent/baselines/behavioral.py — API-response / DOM-structure baselines.

Given an observed XHR call or a page's DOM snapshot, produces a normalized
fingerprint (not the raw data — just the *shape*) and stores that as the
approved baseline. Diffing compares shape equivalence, so content changes
(e.g. timestamps, user names) don't trigger false positives while a removed
field or a type change does.

Normalization:
  * JSON responses → recursively replace leaf values with type markers:
      "<str>", "<int>", "<float>", "<bool>", "<null>"
      Arrays collapse to `[<shape_of_first_element>, "..."]`
  * HTML DOM → tag+attr skeleton, no text content, no class attribute values
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional

from agent.oracles.confirmed import Baseline, hash_artifact, approve


def _shape(obj: Any) -> Any:
    if obj is None:
        return "<null>"
    if isinstance(obj, bool):
        return "<bool>"
    if isinstance(obj, int):
        return "<int>"
    if isinstance(obj, float):
        return "<float>"
    if isinstance(obj, str):
        return "<str>"
    if isinstance(obj, list):
        if not obj:
            return []
        return [_shape(obj[0]), "..."]
    if isinstance(obj, dict):
        return {k: _shape(v) for k, v in sorted(obj.items())}
    return f"<{type(obj).__name__}>"


def shape_of_json(raw: bytes | str) -> dict:
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    try:
        return {"shape": _shape(json.loads(raw))}
    except Exception:
        return {"shape": "<non_json>"}


_HTML_SKELETON = re.compile(r">[^<]*<")


def shape_of_html(html: str) -> dict:
    """Very approximate HTML skeleton: strip text nodes + attribute values."""
    s = _HTML_SKELETON.sub("><", html or "")
    s = re.sub(r'="[^"]*"', "=_", s)       # wipe attr values
    s = re.sub(r"\s+", " ", s)
    return {"skeleton_len": len(s), "skeleton_head": s[:500]}


def capture_behavioral_baseline(*, scope: str, payload_shape: dict,
                                tenant_id: str = "default",
                                approved_by: str = "",
                                auto_approve: bool = False) -> Baseline:
    shape_bytes = json.dumps(payload_shape, sort_keys=True).encode("utf-8")
    h = hash_artifact(shape_bytes)
    baseline = Baseline(
        id=f"{tenant_id}::behavioral::{scope}",
        kind="behavioral",
        scope=scope,
        hash=h,
        approved_by=approved_by or ("auto" if auto_approve else ""),
        tenant_id=tenant_id,
        meta={"shape": payload_shape},
    )
    if auto_approve or approved_by:
        approve(baseline)
    return baseline
