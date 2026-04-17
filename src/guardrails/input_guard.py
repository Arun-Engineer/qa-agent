"""security/input_guard.py — Input Validation & Sanitization Guard"""
from __future__ import annotations
import re, structlog
from dataclasses import dataclass
from enum import Enum
logger = structlog.get_logger()

class ThreatLevel(str, Enum):
    SAFE="safe"; LOW="low"; MEDIUM="medium"; HIGH="high"; BLOCKED="blocked"

@dataclass
class GuardResult:
    is_safe: bool; threat_level: ThreatLevel; sanitized_input: str; threats_detected: list[str]; original_input: str

class InputGuard:
    INJECTION_PATTERNS = [r"ignore\s+(all\s+)?previous\s+instructions", r"you\s+are\s+now\s+(?:a|an)\s+", r"disregard\s+(all\s+)?(?:above|previous|prior)", r"forget\s+(everything|all)\s+(?:above|before|prior)", r"new\s+instructions?\s*:", r"system\s*:\s*you\s+are"]
    PII_PATTERNS = {"credit_card": r"\b(?:\d{4}[-\s]?){3}\d{4}\b", "ssn": r"\b\d{3}[-\s]?\d{2}[-\s]?\d{4}\b", "email": r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b", "phone": r"\b(?:\+\d{1,3}[-\s]?)?\(?\d{3}\)?[-\s]?\d{3}[-\s]?\d{4}\b"}
    PATH_PATTERNS = [r"\.\./", r"\.\.\\", r"/etc/(?:passwd|shadow|hosts)", r"(?:cmd|powershell)\.exe"]
    MAX_INPUT_LENGTH = 50_000

    def __init__(self, strict_mode: bool = False):
        self.strict = strict_mode
        self._inj = [re.compile(p, re.IGNORECASE) for p in self.INJECTION_PATTERNS]
        self._path = [re.compile(p, re.IGNORECASE) for p in self.PATH_PATTERNS]
        self._pii = {k: re.compile(p) for k, p in self.PII_PATTERNS.items()}

    def check(self, input_text: str) -> GuardResult:
        threats: list[str] = []; sanitized = input_text
        if len(input_text) > self.MAX_INPUT_LENGTH:
            threats.append(f"input_too_long ({len(input_text)} chars)"); sanitized = input_text[:self.MAX_INPUT_LENGTH]
        for p in self._inj:
            if p.search(input_text): threats.append("prompt_injection_detected"); break
        for p in self._path:
            if p.search(input_text): threats.append("path_traversal_attempt"); break
        for pii_type, p in self._pii.items():
            if p.findall(input_text):
                threats.append(f"pii_detected_{pii_type}"); sanitized = p.sub(f"[REDACTED_{pii_type.upper()}]", sanitized)
        if "prompt_injection_detected" in threats: level = ThreatLevel.HIGH if self.strict else ThreatLevel.MEDIUM
        elif any("pii_detected" in t for t in threats): level = ThreatLevel.MEDIUM
        elif "path_traversal_attempt" in threats: level = ThreatLevel.HIGH
        elif threats: level = ThreatLevel.LOW
        else: level = ThreatLevel.SAFE
        return GuardResult(is_safe=level in (ThreatLevel.SAFE, ThreatLevel.LOW), threat_level=level, sanitized_input=sanitized, threats_detected=threats, original_input=input_text)
