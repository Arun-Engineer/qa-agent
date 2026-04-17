"""evaluation/online_monitor.py — Online Quality Monitor"""
from __future__ import annotations
import time, structlog
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any
logger = structlog.get_logger()

@dataclass
class QualitySignal:
    workflow: str; run_id: str; success: bool; quality_score: float; latency_ms: float
    token_count: int = 0; user_feedback: int|None = None; timestamp: float = 0.0

class OnlineMonitor:
    def __init__(self, window_size: int=100, success_threshold: float=0.8, quality_threshold: float=0.6, latency_threshold_ms: float=30_000):
        self._signals: dict[str, deque] = defaultdict(lambda: deque(maxlen=window_size))
        self.success_threshold=success_threshold; self.quality_threshold=quality_threshold; self.latency_threshold=latency_threshold_ms

    def record(self, signal: QualitySignal):
        if not signal.timestamp: signal.timestamp = time.time()
        self._signals[signal.workflow].append(signal)

    def get_health(self, workflow: str|None=None) -> dict[str, Any]:
        if workflow: return self._wf_health(workflow)
        healths = {wf: self._wf_health(wf) for wf in self._signals}
        return {"workflows": healths, "alerts": [a for h in healths.values() for a in h.get("alerts",[])]}

    def _wf_health(self, wf: str) -> dict:
        sigs = list(self._signals.get(wf, []))
        if not sigs: return {"workflow":wf,"total_runs":0,"status":"no_data","alerts":[]}
        n = len(sigs); sr = sum(1 for s in sigs if s.success)/n; aq = sum(s.quality_score for s in sigs)/n; al = sum(s.latency_ms for s in sigs)/n
        alerts = []
        if sr < self.success_threshold: alerts.append(f"Low success rate: {sr:.1%}")
        if aq < self.quality_threshold: alerts.append(f"Low quality: {aq:.2f}")
        if al > self.latency_threshold: alerts.append(f"High latency: {al:.0f}ms")
        if n >= 20:
            w = n // 5; er = sum(1 for s in sigs[:w] if s.success)/w; rr = sum(1 for s in sigs[-w:] if s.success)/w
            if er - rr > 0.2: alerts.append(f"Quality drift: {er:.1%} → {rr:.1%}")
        return {"workflow":wf,"total_runs":n,"success_rate":round(sr,3),"avg_quality":round(aq,3),"avg_latency_ms":round(al,1),"alerts":alerts,"status":"healthy" if not alerts else "degraded"}
