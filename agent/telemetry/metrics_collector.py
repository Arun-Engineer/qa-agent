# agent/telemetry/metrics_collector.py
from prometheus_client import Counter, Histogram, start_http_server
import time

# Core Prometheus metrics
step_duration = Histogram("qa_agent_step_duration_seconds", "Duration of each test step", ["tool"])
step_counter = Counter("qa_agent_step_total", "Number of test steps executed", ["tool", "status"])

# Startup Prometheus metrics server on port 9091
start_http_server(9091)

def record_step(tool: str, duration: float, status: str):
    step_duration.labels(tool).observe(duration)
    step_counter.labels(tool, status).inc()



