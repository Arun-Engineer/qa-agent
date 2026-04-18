"""agent/oracles/configured.py — Tenant-specified correctness rules.

A hypothesis that earns enough confirmations is promoted from 'inferred' to
'configured' — meaning it's now a hard rule the tenant has effectively agreed
to. Configured rules can also be hand-authored by admins (future admin UI).

Storage: JSON at data/logs/configured_rules.json, keyed by tenant.
A production deployment would back this with the existing tenancy DB.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

from agent.oracles.base import Finding


_STORE = Path(os.getenv("AUTO_CONFIGURED_RULES", "data/logs/configured_rules.json"))


@dataclass
class ConfiguredRule:
    id: str
    statement: str
    scope: str
    tenant_id: str = "default"
    created_from: str = "inferred_promotion"


def _load() -> dict[str, ConfiguredRule]:
    if not _STORE.exists():
        return {}
    try:
        return {k: ConfiguredRule(**v) for k, v in json.loads(_STORE.read_text("utf-8")).items()}
    except Exception:
        return {}


def _save(rules: dict[str, ConfiguredRule]) -> None:
    _STORE.parent.mkdir(parents=True, exist_ok=True)
    _STORE.write_text(json.dumps({k: asdict(v) for k, v in rules.items()}, indent=2),
                      encoding="utf-8")


def promote_hypothesis(h) -> None:
    """Called by inferred.py when a hypothesis crosses the confidence threshold."""
    rules = _load()
    rule = ConfiguredRule(
        id=h.id, statement=h.statement, scope=h.scope,
        tenant_id=h.tenant_id, created_from="inferred_promotion",
    )
    rules[h.id] = rule
    _save(rules)


def add_rule(statement: str, scope: str, *, tenant_id: str = "default",
             rule_id: str = "") -> ConfiguredRule:
    """Admin API entrypoint: register a rule by hand."""
    rules = _load()
    rid = rule_id or f"{tenant_id}::manual::{len(rules)+1}"
    rule = ConfiguredRule(id=rid, statement=statement, scope=scope,
                          tenant_id=tenant_id, created_from="manual")
    rules[rid] = rule
    _save(rules)
    return rule


def run_configured(model, *, tenant_id: str = "default") -> list[Finding]:
    """Emit findings that represent each configured rule as a must-verify
    invariant for this run."""
    rules = _load()
    findings: list[Finding] = []
    for rule in rules.values():
        if rule.tenant_id != tenant_id:
            continue
        findings.append(Finding(
            source=f"configured:{rule.id}", severity="configured",
            kind="hypothesis", title=rule.statement,
            detail=f"Tenant rule ({rule.created_from}). Scope: {rule.scope}.",
            url=rule.scope, oracle="configured", confidence=1.0,
            evidence={"rule_id": rule.id},
        ))
    return findings
