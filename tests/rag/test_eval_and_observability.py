import pytest, time
from src.evaluation.golden_dataset import GoldenDataset, GoldenCase
from src.evaluation.offline_eval import OfflineEvaluator
from src.evaluation.online_monitor import OnlineMonitor, QualitySignal
from monitoring.tracer import Tracer
from monitoring.feedback import FeedbackCollector, FeedbackEntry
from monitoring.cost_tracker import CostTracker, CostEntry

class TestGoldenDataset:
    def test_default(self, tmp_path):
        assert GoldenDataset(path=str(tmp_path/"g.json")).count >= 4
    def test_filter(self, tmp_path):
        ds = GoldenDataset(path=str(tmp_path/"g.json"))
        assert all(c.category == "ui_test" for c in ds.get_cases(category="ui_test"))
    def test_login_wall_case(self, tmp_path):
        cases = GoldenDataset(path=str(tmp_path/"g.json")).get_cases(tags=["login_wall"])
        assert len(cases) >= 1 and cases[0].expected_output.get("must_not_only_test_login")

class TestOfflineEvaluator:
    def test_run(self, tmp_path):
        r = OfflineEvaluator(golden_dataset=GoldenDataset(path=str(tmp_path/"g.json")), results_dir=str(tmp_path/"r")).run_eval(category="ui_test")
        assert r.total_cases >= 1 and 0 <= r.avg_score <= 1.0

class TestOnlineMonitor:
    def test_health(self):
        m = OnlineMonitor()
        for i in range(10): m.record(QualitySignal(workflow="ui_test",run_id=f"r{i}",success=i<8,quality_score=0.7+i*0.02,latency_ms=2000))
        assert m.get_health("ui_test")["success_rate"] == 0.8
    def test_drift(self):
        m = OnlineMonitor(window_size=100)
        for i in range(15): m.record(QualitySignal(workflow="ui_test",run_id=f"r{i}",success=True,quality_score=0.9,latency_ms=1000))
        for i in range(15,30): m.record(QualitySignal(workflow="ui_test",run_id=f"r{i}",success=i>27,quality_score=0.3,latency_ms=5000))
        assert any("drift" in a.lower() for a in m.get_health("ui_test")["alerts"])

class TestTracer:
    def test_basic(self):
        t = Tracer(); tid = t.start_trace("ui_test")
        with t.span(tid, "rewrite") as s: s.llm_calls=1; s.tokens_used=150; time.sleep(0.01)
        with t.span(tid, "retrieval") as s: time.sleep(0.01)
        trace = t.end_trace(tid)
        assert len(trace.spans)==2 and trace.total_llm_calls==1 and trace.spans[0].duration_ms > 0
    def test_error(self):
        t = Tracer(); tid = t.start_trace("api_test")
        try:
            with t.span(tid, "fail"): raise ValueError("broke")
        except ValueError: pass
        assert t.end_trace(tid).spans[0].status == "error"
    def test_chain_steps(self):
        t = Tracer(); tid = t.start_trace("ui_test")
        for name in ["understand_spec","discover_site","plan_tests","generate_auth","generate_tests","self_review_code","execute_tests","analyze_results","generate_report"]:
            with t.span(tid, name) as s: s.llm_calls = 1 if "generate" in name or "analyze" in name else 0; s.tokens_used = 500 if s.llm_calls else 0
        trace = t.end_trace(tid)
        assert len(trace.spans) == 9 and trace.total_llm_calls >= 3

class TestFeedback:
    def test_stats(self):
        c = FeedbackCollector()
        c.record(FeedbackEntry(run_id="r1",workflow="ui_test",rating=5,category="helpful"))
        c.record(FeedbackEntry(run_id="r2",workflow="ui_test",rating=2,category="wrong_tests"))
        c.record(FeedbackEntry(run_id="r3",workflow="ui_test",rating=4,category="helpful"))
        s = c.get_stats("ui_test")
        assert s["count"]==3 and s["avg_rating"]==pytest.approx(3.67,abs=0.01)

class TestCostTracker:
    def test_estimate(self):
        assert 0 < CostTracker.estimate_cost("gpt-4o-mini",1000,500) < 0.01
    def test_summary(self):
        t = CostTracker()
        t.record(CostEntry(run_id="r1",workflow="ui_test",tenant_id="t1",model="gpt-4o-mini",input_tokens=1000,output_tokens=500,cost_usd=0,stage="planning"))
        t.record(CostEntry(run_id="r1",workflow="ui_test",tenant_id="t1",model="gpt-4o-mini",input_tokens=2000,output_tokens=800,cost_usd=0,stage="generation"))
        s = t.get_summary(tenant_id="t1")
        assert s["runs"]==1 and s["total_tokens"]>0 and s["total_cost_usd"]>0
