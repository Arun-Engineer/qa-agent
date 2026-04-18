"""agent/oracles/base.py — Common types for the oracle layer."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol


Severity = Literal["universal", "inferred", "configured", "confirmed", "noise", "pending"]
Kind = Literal["bug", "regression", "flake", "noise", "hypothesis"]


@dataclass
class Finding:
    """One observation + its classification."""
    source: str                      # step_index or "universal" | "inferred:<id>"
    severity: Severity = "noise"
    kind: Kind = "noise"
    title: str = ""
    detail: str = ""
    url: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)
    confidence: float = 1.0
    oracle: str = ""                 # which oracle produced this

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "severity": self.severity,
            "kind": self.kind,
            "title": self.title,
            "detail": self.detail,
            "url": self.url,
            "evidence": self.evidence,
            "confidence": self.confidence,
            "oracle": self.oracle,
        }


class Oracle(Protocol):
    name: str

    def check(self, context: dict[str, Any]) -> list[Finding]:
        ...
