"""evaluation/golden_dataset.py — Golden Test Set Management"""
from __future__ import annotations
import json, structlog
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
logger = structlog.get_logger()

@dataclass
class GoldenCase:
    id: str; category: str; input_spec: str; expected_output: dict[str, Any]
    metadata: dict[str, Any] = field(default_factory=dict); tags: list[str] = field(default_factory=list)

@dataclass
class EvalScore:
    case_id: str; passed: bool; score: float; details: dict[str, Any] = field(default_factory=dict); actual_output: str = ""

class GoldenDataset:
    def __init__(self, path: str = "evaluation/golden_dataset.json"):
        self._path = Path(path); self._cases: dict[str, GoldenCase] = {}; self._load()

    def _load(self):
        if not self._path.exists(): self._create_default(); return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            for item in data.get("cases", []): c = GoldenCase(**item); self._cases[c.id] = c
        except Exception as e:
            logger.warning("golden_dataset_load_failed", path=str(self._path), error=str(e))
            self._create_default()

    def _create_default(self):
        cases = [
            GoldenCase(id="golden_001", category="ui_test", input_spec="Test login page: valid credentials redirect, invalid show error, empty fields show validation", expected_output={"min_scenarios":3,"must_include":["valid_login","invalid_login","empty_fields"]}, tags=["login","authentication"]),
            GoldenCase(id="golden_002", category="ui_test", input_spec="Test cart: add item, update quantity, remove item, apply coupon, checkout", expected_output={"min_scenarios":5,"must_include":["add_to_cart","update_quantity","remove_item","coupon","checkout"],"login_as_prerequisite":True}, tags=["cart","e-commerce"]),
            GoldenCase(id="golden_003", category="spec_review", input_spec="The system shall allow users to log in.", expected_output={"should_flag_ambiguity":True,"min_dimensions_scored":5}, tags=["spec_review","ambiguous"]),
            GoldenCase(id="golden_004", category="api_test", input_spec="Test POST /api/users: create user, reject duplicate email, validate fields, check 201", expected_output={"min_scenarios":4,"must_include":["create_user","duplicate_email","validation","status_code"]}, tags=["api","crud"]),
            GoldenCase(id="golden_005", category="ui_test", input_spec="Test behind login wall: navigate to example.com, login, then test dashboard widgets: chart, date filter, export CSV", expected_output={"min_scenarios":4,"login_as_prerequisite":True,"must_include":["login_step","chart_rendering","date_filter","csv_export"],"must_not_only_test_login":True}, tags=["login_wall","dashboard","authenticated"]),
        ]
        self._cases = {c.id: c for c in cases}; self.save()

    def save(self):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data = {"cases": [{"id":c.id,"category":c.category,"input_spec":c.input_spec,"expected_output":c.expected_output,"metadata":c.metadata,"tags":c.tags} for c in self._cases.values()]}
        self._path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def get_cases(self, category: str|None=None, tags: list[str]|None=None) -> list[GoldenCase]:
        cases = list(self._cases.values())
        if category: cases = [c for c in cases if c.category == category]
        if tags: cases = [c for c in cases if any(t in c.tags for t in tags)]
        return cases

    def add_case(self, case: GoldenCase): self._cases[case.id] = case; self.save()

    @property
    def count(self) -> int: return len(self._cases)
