"""security/content_filter.py — Retrieved Content Filter"""
from __future__ import annotations
import re, structlog
from dataclasses import dataclass
logger = structlog.get_logger()

@dataclass
class ContentCheckResult:
    doc_id: str; is_safe: bool; issues: list[str]; filtered_text: str

class ContentFilter:
    INJECTION_IN_CONTENT = [r"IMPORTANT:\s*ignore\s+all", r"<s>.*?</s>", r"\[INST\].*?\[/INST\]", r"Human:\s*(?:forget|ignore|override)", r"BEGIN\s+(?:NEW|OVERRIDE)\s+INSTRUCTIONS"]
    TOXIC_PATTERNS = [r"(?:password|secret|token)\s*[:=]\s*\S+", r"(?:BEGIN\s+(?:RSA|DSA|EC)\s+PRIVATE\s+KEY)", r"(?:aws_secret_access_key|AKIA[0-9A-Z]{16})"]

    def __init__(self, max_doc_length: int = 10_000):
        self.max_doc_length = max_doc_length
        self._inj = [re.compile(p, re.IGNORECASE|re.DOTALL) for p in self.INJECTION_IN_CONTENT]
        self._toxic = [re.compile(p, re.IGNORECASE) for p in self.TOXIC_PATTERNS]

    def check(self, doc_id: str, text: str) -> ContentCheckResult:
        issues: list[str] = []; filtered = text
        if len(text) > self.max_doc_length: filtered = text[:self.max_doc_length]; issues.append("truncated")
        for p in self._inj:
            if p.search(text): issues.append("embedded_injection_payload"); filtered = p.sub("[FILTERED]", filtered)
        for p in self._toxic:
            if p.search(text): issues.append("exposed_credentials"); filtered = p.sub("[REDACTED]", filtered)
        return ContentCheckResult(doc_id=doc_id, is_safe="embedded_injection_payload" not in issues, issues=issues, filtered_text=filtered)

    def check_batch(self, documents: list[dict]) -> list[ContentCheckResult]:
        return [self.check(d.get("doc_id",""), d.get("text","")) for d in documents]
