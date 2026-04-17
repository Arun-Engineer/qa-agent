"""observability/tracer.py — Per-Stage Pipeline Tracer"""
from __future__ import annotations
import time, uuid, structlog
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Optional
logger = structlog.get_logger()

@dataclass
class Span:
    name: str; span_id: str = ""; start_time: float = 0.0; end_time: float = 0.0; duration_ms: float = 0.0
    status: str = "ok"; inputs: dict[str, Any] = field(default_factory=dict)
    outputs: dict[str, Any] = field(default_factory=dict); metadata: dict[str, Any] = field(default_factory=dict)
    error: str = ""; llm_calls: int = 0; tokens_used: int = 0
    def __post_init__(self):
        if not self.span_id: self.span_id = uuid.uuid4().hex[:12]

@dataclass
class Trace:
    trace_id: str; workflow: str; tenant_id: str = ""; spans: list[Span] = field(default_factory=list)
    start_time: float = 0.0; end_time: float = 0.0; total_duration_ms: float = 0.0
    total_llm_calls: int = 0; total_tokens: int = 0; status: str = "ok"
    def to_dict(self) -> dict:
        return {"trace_id":self.trace_id,"workflow":self.workflow,"total_duration_ms":self.total_duration_ms,
                "total_llm_calls":self.total_llm_calls,"total_tokens":self.total_tokens,"status":self.status,
                "spans":[{"name":s.name,"duration_ms":s.duration_ms,"status":s.status,"llm_calls":s.llm_calls,"tokens_used":s.tokens_used,"error":s.error} for s in self.spans]}

class SpanContext:
    def __init__(self, tracer, trace_id: str, name: str):
        self._tracer = tracer; self._trace_id = trace_id; self._name = name; self._span: Span|None = None
    def __enter__(self) -> Span:
        self._span = Span(name=self._name, start_time=time.time()); return self._span
    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._span:
            if exc_type: self._span.status = "error"; self._span.error = str(exc_val)
            self._span.end_time = time.time(); self._span.duration_ms = round((self._span.end_time - self._span.start_time)*1000, 1)
            if self._trace_id in self._tracer._active: self._tracer._active[self._trace_id].spans.append(self._span)
        return False

class Tracer:
    def __init__(self, max_traces: int = 500):
        self._active: dict[str, Trace] = {}; self._completed: deque = deque(maxlen=max_traces)

    def start_trace(self, workflow: str, tenant_id: str = "", **meta) -> str:
        tid = f"tr_{uuid.uuid4().hex[:12]}"
        self._active[tid] = Trace(trace_id=tid, workflow=workflow, tenant_id=tenant_id, start_time=time.time())
        return tid

    def span(self, trace_id: str, name: str) -> SpanContext:
        return SpanContext(self, trace_id, name)

    def end_trace(self, trace_id: str) -> Trace|None:
        trace = self._active.pop(trace_id, None)
        if not trace: return None
        trace.end_time = time.time(); trace.total_duration_ms = round((trace.end_time - trace.start_time)*1000, 1)
        trace.total_llm_calls = sum(s.llm_calls for s in trace.spans); trace.total_tokens = sum(s.tokens_used for s in trace.spans)
        if any(s.status == "error" for s in trace.spans): trace.status = "partial_error"
        self._completed.append(trace); return trace

    def get_recent_traces(self, workflow: str|None=None, limit: int=20) -> list[dict]:
        traces = list(self._completed)
        if workflow: traces = [t for t in traces if t.workflow == workflow]
        return [t.to_dict() for t in traces[-limit:]]
