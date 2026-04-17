"""evaluation/offline_eval.py — Offline Evaluation Pipeline"""
from __future__ import annotations
import json, time, structlog
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from src.evaluation.golden_dataset import GoldenDataset, GoldenCase, EvalScore
logger = structlog.get_logger()

@dataclass
class EvalReport:
    run_id: str; timestamp: str; category: str; total_cases: int; passed: int; failed: int
    scores: list[EvalScore]; avg_score: float; duration_ms: float
    @property
    def pass_rate(self) -> float: return round(self.passed / max(self.total_cases, 1) * 100, 1)
    @property
    def summary(self) -> str: return f"Eval {self.run_id}: {self.passed}/{self.total_cases} ({self.pass_rate}%) avg={self.avg_score:.2f}"

class OfflineEvaluator:
    def __init__(self, golden_dataset: GoldenDataset|None=None, results_dir: str="evaluation/eval_results"):
        self.dataset = golden_dataset or GoldenDataset(); self.results_dir = Path(results_dir); self.results_dir.mkdir(parents=True, exist_ok=True)

    def run_eval(self, category: str|None=None, tags: list[str]|None=None, agent_fn=None) -> EvalReport:
        cases = self.dataset.get_cases(category=category, tags=tags)
        if not cases: return EvalReport(run_id="empty",timestamp=datetime.utcnow().isoformat(),category=category or "all",total_cases=0,passed=0,failed=0,scores=[],avg_score=0,duration_ms=0)
        start = time.time(); scores = [self._eval_case(c, agent_fn) for c in cases]
        passed = sum(1 for s in scores if s.passed); avg = sum(s.score for s in scores)/len(scores)
        return EvalReport(run_id=f"eval_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}",timestamp=datetime.utcnow().isoformat(),category=category or "all",total_cases=len(cases),passed=passed,failed=len(cases)-passed,scores=scores,avg_score=round(avg,3),duration_ms=round((time.time()-start)*1000,1))

    def _eval_case(self, case: GoldenCase, agent_fn) -> EvalScore:
        try:
            actual = agent_fn(case.input_spec) if agent_fn else {"scenarios":[{"name":w} for w in case.input_spec.lower().split() if len(w)>4][:5]}
            score, details = self._score(case.expected_output, actual)
            return EvalScore(case_id=case.id, passed=score>=0.7, score=score, details=details)
        except Exception as e: return EvalScore(case_id=case.id, passed=False, score=0.0, details={"error":str(e)})

    def _score(self, expected: dict, actual: Any) -> tuple[float, dict]:
        if not isinstance(actual, dict): return 0.0, {"error":"not dict"}
        checks, total, passed = [], 0, 0
        if "min_scenarios" in expected:
            total += 1; n = len(actual.get("scenarios",actual.get("steps",[]))); ok = n >= expected["min_scenarios"]; passed += int(ok); checks.append(f"scenarios {n} {'≥' if ok else '<'} {expected['min_scenarios']}")
        if "must_include" in expected:
            txt = json.dumps(actual).lower()
            for item in expected["must_include"]: total += 1; ok = item.lower() in txt; passed += int(ok); checks.append(f"{'✓' if ok else '✗'} {item}")
        if expected.get("must_not_only_test_login"):
            total += 1; scenarios = actual.get("scenarios",actual.get("steps",[])); login_only = all("login" in json.dumps(s).lower() for s in scenarios) if scenarios else True; passed += int(not login_only); checks.append(f"{'✓' if not login_only else '✗'} beyond login")
        return round(passed/max(total,1),3), {"checks":checks}
