"""observability/cost_tracker.py — LLM Cost Tracking"""
from __future__ import annotations
import time, structlog
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any
logger = structlog.get_logger()

MODEL_PRICING = {"gpt-4o":{"input":0.005,"output":0.015},"gpt-4o-mini":{"input":0.00015,"output":0.0006},"claude-sonnet-4-20250514":{"input":0.003,"output":0.015},"claude-3-5-haiku-20241022":{"input":0.001,"output":0.005}}

@dataclass
class CostEntry:
    run_id: str; workflow: str; tenant_id: str; model: str; input_tokens: int; output_tokens: int
    cost_usd: float; stage: str = ""; timestamp: float = 0.0

class CostTracker:
    def __init__(self, max_entries: int = 5000):
        self._entries: deque[CostEntry] = deque(maxlen=max_entries)
        self._by_tenant: dict[str, list[CostEntry]] = defaultdict(list)
        self._by_wf: dict[str, list[CostEntry]] = defaultdict(list)

    @staticmethod
    def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
        p = MODEL_PRICING.get(model, {"input":0.001,"output":0.002})
        return round((input_tokens/1000)*p["input"]+(output_tokens/1000)*p["output"], 6)

    def record(self, entry: CostEntry):
        if not entry.timestamp: entry.timestamp = time.time()
        if not entry.cost_usd: entry.cost_usd = self.estimate_cost(entry.model, entry.input_tokens, entry.output_tokens)
        self._entries.append(entry); self._by_tenant[entry.tenant_id].append(entry); self._by_wf[entry.workflow].append(entry)

    def get_summary(self, tenant_id: str|None=None, workflow: str|None=None, last_n_hours: int=24) -> dict[str, Any]:
        cutoff = time.time() - last_n_hours*3600
        if tenant_id: entries = [e for e in self._by_tenant.get(tenant_id,[]) if e.timestamp > cutoff]
        elif workflow: entries = [e for e in self._by_wf.get(workflow,[]) if e.timestamp > cutoff]
        else: entries = [e for e in self._entries if e.timestamp > cutoff]
        if not entries: return {"total_cost":0,"total_tokens":0,"runs":0}
        tc = sum(e.cost_usd for e in entries); ti = sum(e.input_tokens for e in entries); to = sum(e.output_tokens for e in entries)
        runs = len(set(e.run_id for e in entries))
        bm = defaultdict(lambda:{"cost":0.0,"calls":0,"tokens":0})
        for e in entries: bm[e.model]["cost"]+=e.cost_usd; bm[e.model]["calls"]+=1; bm[e.model]["tokens"]+=e.input_tokens+e.output_tokens
        bs = defaultdict(lambda:{"cost":0.0,"calls":0})
        for e in entries:
            if e.stage: bs[e.stage]["cost"]+=e.cost_usd; bs[e.stage]["calls"]+=1
        return {"total_cost_usd":round(tc,4),"total_input_tokens":ti,"total_output_tokens":to,"total_tokens":ti+to,"runs":runs,"avg_cost_per_run":round(tc/max(runs,1),4),"by_model":dict(bm),"by_stage":dict(bs)}
