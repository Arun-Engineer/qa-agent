"""security/output_filter.py — LLM Output Validation Filter"""
from __future__ import annotations
import re, structlog
from dataclasses import dataclass
logger = structlog.get_logger()

@dataclass
class OutputCheckResult:
    is_safe: bool; issues: list[str]; filtered_output: str; confidence: float

class OutputFilter:
    LEAK_PATTERNS = [r"(?:my\s+)?(?:api[_\s]?key|password|secret|token)\s*(?:is|=|:)\s*\S{8,}", r"sk-[a-zA-Z0-9]{20,}", r"ghp_[a-zA-Z0-9]{36}"]
    HALLUCINATION_PATTERNS = [r"(?:I\s+(?:don't|do not)\s+have\s+(?:access|information))", r"(?:As\s+an?\s+AI\s+(?:language\s+)?model)"]
    DANGEROUS_CODE = [r"(?:os\.system|subprocess\.(?:call|run|Popen))\s*\(", r"(?:eval|exec)\s*\(", r"(?:shutil\.rmtree|os\.remove(?:dirs)?)\s*\("]

    def __init__(self, max_output_length: int = 50_000, check_code: bool = True):
        self.max_length = max_output_length; self.check_code = check_code
        self._leak = [re.compile(p, re.IGNORECASE) for p in self.LEAK_PATTERNS]
        self._hall = [re.compile(p, re.IGNORECASE) for p in self.HALLUCINATION_PATTERNS]
        self._dang = [re.compile(p) for p in self.DANGEROUS_CODE]

    def check(self, output: str) -> OutputCheckResult:
        issues: list[str] = []; filtered = output; conf = 1.0
        if len(output) > self.max_length: filtered = output[:self.max_length]; issues.append("output_truncated")
        if len(output.strip()) < 10: issues.append("output_too_short"); conf = 0.3
        for p in self._leak:
            if p.search(output): issues.append("potential_credential_leak"); filtered = p.sub("[REDACTED]", filtered); conf = min(conf, 0.4)
        for p in self._hall:
            if p.search(output): issues.append("hallucination_indicator"); conf = min(conf, 0.6)
        if self.check_code and any(k in output for k in ["def ","class ","import ","```python"]):
            for p in self._dang:
                if p.search(output): issues.append("dangerous_code_pattern"); conf = min(conf, 0.5)
        is_safe = not any(i in ("potential_credential_leak","dangerous_code_pattern") for i in issues)
        return OutputCheckResult(is_safe=is_safe, issues=issues, filtered_output=filtered, confidence=conf)
