"""
Phase 6 · Release Gate
Pass/Fail/Warn decision engine with confidence scores for CI/CD pipelines.
Configurable rules, threshold profiles, and audit trail.
"""

import json
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime

logger = logging.getLogger(__name__)


class GateVerdict(Enum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"


class RuleCategory(Enum):
    PASS_RATE = "pass_rate"
    CRITICAL_BUGS = "critical_bugs"
    COVERAGE = "coverage"
    FLAKY_TESTS = "flaky_tests"
    REGRESSION = "regression"
    PERFORMANCE = "performance"
    SECURITY = "security"
    ACCESSIBILITY = "accessibility"
    CUSTOM = "custom"


@dataclass
class GateRule:
    """Single gate evaluation rule."""
    rule_id: str
    category: RuleCategory
    name: str
    fail_threshold: float  # below this → FAIL
    warn_threshold: float  # below this → WARN
    weight: float = 1.0
    enabled: bool = True
    description: str = ""


@dataclass
class RuleResult:
    """Outcome of evaluating a single rule."""
    rule: GateRule
    actual_value: float
    verdict: GateVerdict
    confidence: float  # 0.0–1.0
    detail: str = ""


@dataclass
class GateDecision:
    """Aggregate release gate decision."""
    overall_verdict: GateVerdict
    overall_score: float  # 0–100 weighted
    confidence: float
    rule_results: List[RuleResult]
    blocking_rules: List[RuleResult]
    warning_rules: List[RuleResult]
    evaluated_at: datetime = field(default_factory=datetime.utcnow)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "verdict": self.overall_verdict.value,
            "score": round(self.overall_score, 2),
            "confidence": round(self.confidence, 3),
            "blocking_count": len(self.blocking_rules),
            "warning_count": len(self.warning_rules),
            "evaluated_at": self.evaluated_at.isoformat(),
            "rules": [
                {
                    "rule_id": rr.rule.rule_id,
                    "name": rr.rule.name,
                    "category": rr.rule.category.value,
                    "actual": round(rr.actual_value, 2),
                    "fail_threshold": rr.rule.fail_threshold,
                    "warn_threshold": rr.rule.warn_threshold,
                    "verdict": rr.verdict.value,
                    "confidence": round(rr.confidence, 3),
                    "detail": rr.detail,
                }
                for rr in self.rule_results
            ],
            "metadata": self.metadata,
        }

    @property
    def ci_exit_code(self) -> int:
        """0 = pass, 1 = fail, 2 = warn (non-blocking)."""
        if self.overall_verdict == GateVerdict.PASS:
            return 0
        if self.overall_verdict == GateVerdict.FAIL:
            return 1
        return 2


# ── default rule profiles ──────────────────────────────────────

PROFILE_STRICT: List[GateRule] = [
    GateRule("pr_pass", RuleCategory.PASS_RATE, "Pass Rate", fail_threshold=95, warn_threshold=98, weight=3),
    GateRule("pr_crit", RuleCategory.CRITICAL_BUGS, "Zero Critical Bugs", fail_threshold=1, warn_threshold=1, weight=5),
    GateRule("pr_cov", RuleCategory.COVERAGE, "Test Coverage", fail_threshold=80, warn_threshold=90, weight=2),
    GateRule("pr_flaky", RuleCategory.FLAKY_TESTS, "Flaky Test Limit", fail_threshold=5, warn_threshold=2, weight=1),
    GateRule("pr_regr", RuleCategory.REGRESSION, "No Regressions", fail_threshold=1, warn_threshold=1, weight=4),
    GateRule("pr_perf", RuleCategory.PERFORMANCE, "P95 Response Time", fail_threshold=3000, warn_threshold=2000, weight=1),
    GateRule("pr_sec", RuleCategory.SECURITY, "No High Vulns", fail_threshold=1, warn_threshold=1, weight=5),
    GateRule("pr_a11y", RuleCategory.ACCESSIBILITY, "WCAG AA Compliance", fail_threshold=90, warn_threshold=95, weight=1),
]

PROFILE_STANDARD: List[GateRule] = [
    GateRule("std_pass", RuleCategory.PASS_RATE, "Pass Rate", fail_threshold=85, warn_threshold=92, weight=3),
    GateRule("std_crit", RuleCategory.CRITICAL_BUGS, "Critical Bugs ≤ 0", fail_threshold=1, warn_threshold=1, weight=4),
    GateRule("std_cov", RuleCategory.COVERAGE, "Test Coverage", fail_threshold=70, warn_threshold=80, weight=2),
    GateRule("std_flaky", RuleCategory.FLAKY_TESTS, "Flaky Test Limit", fail_threshold=10, warn_threshold=5, weight=1),
    GateRule("std_regr", RuleCategory.REGRESSION, "Regression Limit", fail_threshold=3, warn_threshold=1, weight=3),
]

PROFILE_RELAXED: List[GateRule] = [
    GateRule("rel_pass", RuleCategory.PASS_RATE, "Pass Rate", fail_threshold=70, warn_threshold=80, weight=2),
    GateRule("rel_crit", RuleCategory.CRITICAL_BUGS, "Critical Bugs ≤ 2", fail_threshold=3, warn_threshold=1, weight=3),
    GateRule("rel_cov", RuleCategory.COVERAGE, "Test Coverage", fail_threshold=50, warn_threshold=65, weight=1),
]

PROFILES: Dict[str, List[GateRule]] = {
    "strict": PROFILE_STRICT,
    "standard": PROFILE_STANDARD,
    "relaxed": PROFILE_RELAXED,
}


class ReleaseGate:
    """
    CI/CD release gate evaluator.
    Evaluates test run data against configurable rules and produces
    a Pass/Warn/Fail decision with confidence scoring.
    """

    def __init__(self, rules: Optional[List[GateRule]] = None, profile: str = "standard"):
        if rules:
            self.rules = rules
        else:
            self.rules = PROFILES.get(profile, PROFILE_STANDARD)

    def evaluate(self, run_data: Dict[str, Any]) -> GateDecision:
        """Evaluate run data against all enabled rules."""
        results = run_data.get("results", [])
        bugs = run_data.get("bugs", [])
        metrics = self._extract_metrics(results, bugs, run_data)

        rule_results: List[RuleResult] = []
        for rule in self.rules:
            if not rule.enabled:
                continue
            rr = self._evaluate_rule(rule, metrics)
            rule_results.append(rr)

        blocking = [r for r in rule_results if r.verdict == GateVerdict.FAIL]
        warnings = [r for r in rule_results if r.verdict == GateVerdict.WARN]

        # Weighted score
        total_weight = sum(r.rule.weight for r in rule_results) or 1
        weighted_score = sum(
            r.rule.weight * (100 if r.verdict == GateVerdict.PASS else 50 if r.verdict == GateVerdict.WARN else 0)
            for r in rule_results
        ) / total_weight

        # Overall confidence
        confidence = (
            sum(r.confidence * r.rule.weight for r in rule_results) / total_weight
            if rule_results else 0.0
        )

        # Overall verdict
        if blocking:
            verdict = GateVerdict.FAIL
        elif warnings:
            verdict = GateVerdict.WARN
        else:
            verdict = GateVerdict.PASS

        decision = GateDecision(
            overall_verdict=verdict,
            overall_score=weighted_score,
            confidence=confidence,
            rule_results=rule_results,
            blocking_rules=blocking,
            warning_rules=warnings,
            metadata={"profile": self._profile_name(), "metrics": metrics},
        )

        logger.info("Release gate: %s (score=%.1f, confidence=%.2f, %d blocking, %d warnings)",
                     verdict.value, weighted_score, confidence, len(blocking), len(warnings))
        return decision

    def evaluate_for_ci(self, run_data: Dict[str, Any]) -> Tuple[int, str]:
        """Returns (exit_code, summary_message) for CI/CD integration."""
        decision = self.evaluate(run_data)
        summary = (
            f"Release Gate: {decision.overall_verdict.value} "
            f"(score={decision.overall_score:.1f}%, confidence={decision.confidence:.0%})"
        )
        if decision.blocking_rules:
            summary += f"\nBlockers: {', '.join(r.rule.name for r in decision.blocking_rules)}"
        if decision.warning_rules:
            summary += f"\nWarnings: {', '.join(r.rule.name for r in decision.warning_rules)}"
        return decision.ci_exit_code, summary

    # ── metrics extraction ─────────────────────────────────────

    def _extract_metrics(
        self, results: List[Dict], bugs: List[Dict], run_data: Dict,
    ) -> Dict[str, float]:
        total = len(results)
        passed = sum(1 for r in results if r.get("status") == "passed")
        failed = sum(1 for r in results if r.get("status") == "failed")
        critical_bugs = sum(1 for b in bugs if b.get("severity") in ("critical", "1 - Critical"))
        high_bugs = sum(1 for b in bugs if b.get("severity") in ("high", "2 - High"))
        flaky = sum(1 for r in results if r.get("flaky_count", 0) > 0)
        regressions = sum(1 for r in results if r.get("is_regression", False))
        coverage = run_data.get("coverage_percent", 0)
        p95_response = run_data.get("p95_response_ms", 0)
        security_high = run_data.get("security_high_vulns", 0)
        a11y_score = run_data.get("accessibility_score", 100)

        return {
            "pass_rate": round((passed / total * 100) if total else 0, 2),
            "critical_bugs": critical_bugs,
            "high_bugs": high_bugs,
            "total_bugs": len(bugs),
            "coverage": coverage,
            "flaky_tests": flaky,
            "regressions": regressions,
            "p95_response_ms": p95_response,
            "security_high_vulns": security_high,
            "accessibility_score": a11y_score,
            "total_tests": total,
            "passed": passed,
            "failed": failed,
        }

    def _evaluate_rule(self, rule: GateRule, metrics: Dict[str, float]) -> RuleResult:
        """Evaluate a single rule against extracted metrics."""
        actual = self._get_metric_for_rule(rule, metrics)

        # Inverse rules: lower is better (bugs, flaky, regressions, response time, vulns)
        inverse = rule.category in (
            RuleCategory.CRITICAL_BUGS, RuleCategory.FLAKY_TESTS,
            RuleCategory.REGRESSION, RuleCategory.PERFORMANCE,
            RuleCategory.SECURITY,
        )

        if inverse:
            # fail_threshold means "fail if actual >= threshold"
            if actual >= rule.fail_threshold:
                verdict = GateVerdict.FAIL
            elif actual >= rule.warn_threshold:
                verdict = GateVerdict.WARN
            else:
                verdict = GateVerdict.PASS
        else:
            # fail_threshold means "fail if actual < threshold"
            if actual < rule.fail_threshold:
                verdict = GateVerdict.FAIL
            elif actual < rule.warn_threshold:
                verdict = GateVerdict.WARN
            else:
                verdict = GateVerdict.PASS

        # Confidence based on data volume
        data_points = metrics.get("total_tests", 0)
        confidence = min(1.0, data_points / 100)  # 100+ tests = full confidence

        detail = f"{rule.name}: actual={actual}, fail@{rule.fail_threshold}, warn@{rule.warn_threshold}"

        return RuleResult(
            rule=rule, actual_value=actual,
            verdict=verdict, confidence=confidence, detail=detail,
        )

    def _get_metric_for_rule(self, rule: GateRule, metrics: Dict[str, float]) -> float:
        mapping = {
            RuleCategory.PASS_RATE: "pass_rate",
            RuleCategory.CRITICAL_BUGS: "critical_bugs",
            RuleCategory.COVERAGE: "coverage",
            RuleCategory.FLAKY_TESTS: "flaky_tests",
            RuleCategory.REGRESSION: "regressions",
            RuleCategory.PERFORMANCE: "p95_response_ms",
            RuleCategory.SECURITY: "security_high_vulns",
            RuleCategory.ACCESSIBILITY: "accessibility_score",
        }
        key = mapping.get(rule.category, "")
        return metrics.get(key, 0)

    def _profile_name(self) -> str:
        for name, rules in PROFILES.items():
            if self.rules is rules:
                return name
        return "custom"