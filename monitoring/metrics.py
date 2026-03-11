"""
monitoring/metrics.py — Application Metrics Collector

Tracks key metrics in-memory for the /api/metrics endpoint and CloudWatch.
Not a replacement for Prometheus — just lightweight counters for the dashboard.

Usage:
    from monitoring.metrics import metrics
    metrics.record_run("api_test", passed=5, failed=1, duration_ms=3200)
    metrics.record_api_call("/api/run", 200, 1500)
    print(metrics.summary())
"""
from __future__ import annotations

import time
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class RunMetric:
    workflow: str
    passed: int
    failed: int
    duration_ms: float
    timestamp: float


@dataclass
class ApiMetric:
    path: str
    status: int
    duration_ms: float
    timestamp: float


class MetricsCollector:
    """Thread-safe in-memory metrics collector."""

    def __init__(self, max_history: int = 1000):
        self._lock = threading.Lock()
        self._runs: List[RunMetric] = []
        self._api_calls: List[ApiMetric] = []
        self._max_history = max_history
        self._counters: Dict[str, int] = defaultdict(int)
        self._start_time = time.time()

    def record_run(
        self, workflow: str, passed: int = 0, failed: int = 0, duration_ms: float = 0
    ):
        """Record a test run completion."""
        with self._lock:
            self._runs.append(RunMetric(
                workflow=workflow, passed=passed, failed=failed,
                duration_ms=duration_ms, timestamp=time.time(),
            ))
            if len(self._runs) > self._max_history:
                self._runs = self._runs[-self._max_history:]
            self._counters["total_runs"] += 1
            self._counters["total_passed"] += passed
            self._counters["total_failed"] += failed

    def record_api_call(self, path: str, status: int, duration_ms: float):
        """Record an API call."""
        with self._lock:
            self._api_calls.append(ApiMetric(
                path=path, status=status,
                duration_ms=duration_ms, timestamp=time.time(),
            ))
            if len(self._api_calls) > self._max_history:
                self._api_calls = self._api_calls[-self._max_history:]
            self._counters["api_calls"] += 1
            if status >= 500:
                self._counters["api_errors"] += 1

    def record_event(self, event: str):
        """Record a generic counter event."""
        with self._lock:
            self._counters[event] += 1

    def summary(self) -> Dict[str, Any]:
        """Get current metrics summary for dashboard/API."""
        with self._lock:
            total_runs = self._counters.get("total_runs", 0)
            total_passed = self._counters.get("total_passed", 0)
            total_failed = self._counters.get("total_failed", 0)
            total = total_passed + total_failed

            # Recent runs (last 10)
            recent = self._runs[-10:] if self._runs else []

            # API stats (last 5 minutes)
            cutoff = time.time() - 300
            recent_api = [a for a in self._api_calls if a.timestamp > cutoff]
            avg_api_ms = (
                sum(a.duration_ms for a in recent_api) / len(recent_api)
                if recent_api else 0
            )

            # Uptime
            uptime_seconds = int(time.time() - self._start_time)

            return {
                "total_runs": total_runs,
                "total_passed": total_passed,
                "total_failed": total_failed,
                "average_pass_rate": round((total_passed / total * 100) if total > 0 else 0, 1),
                "api_calls_5min": len(recent_api),
                "avg_api_latency_ms": round(avg_api_ms, 1),
                "api_errors": self._counters.get("api_errors", 0),
                "uptime_seconds": uptime_seconds,
                "unique_suites": len(set(r.workflow for r in self._runs)),
                "recent_runs": [
                    {
                        "workflow": r.workflow,
                        "passed": r.passed,
                        "failed": r.failed,
                        "duration_ms": r.duration_ms,
                    }
                    for r in recent
                ],
            }

    def reset(self):
        """Reset all counters (for testing)."""
        with self._lock:
            self._runs.clear()
            self._api_calls.clear()
            self._counters.clear()


# Global singleton
metrics = MetricsCollector()
