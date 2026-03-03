"""
src/deep_access/network_capture.py — Network traffic capture via Playwright CDP.

Captures all HTTP requests/responses during test execution:
  - Request/response headers
  - Timing data
  - Bodies (configurable)
  - Exports to HAR format
"""
from __future__ import annotations

import json, time, structlog
from dataclasses import dataclass, field
from typing import Optional
from pathlib import Path

logger = structlog.get_logger()


@dataclass
class CapturedRequest:
    url: str
    method: str
    status: int = 0
    request_headers: dict = field(default_factory=dict)
    response_headers: dict = field(default_factory=dict)
    request_body: str = ""
    response_body: str = ""
    duration_ms: float = 0
    resource_type: str = ""
    timestamp: float = 0


class NetworkCapture:
    """Capture network traffic during Playwright test execution."""

    def __init__(self):
        self.requests: list[CapturedRequest] = []
        self._start_time: float = 0

    def start(self):
        self._start_time = time.time()
        self.requests = []

    def attach_to_page(self, page):
        """Attach request/response listeners to a Playwright page."""
        def on_request(request):
            self.requests.append(CapturedRequest(
                url=request.url,
                method=request.method,
                request_headers=dict(request.headers),
                request_body=request.post_data or "",
                resource_type=request.resource_type,
                timestamp=time.time(),
            ))

        def on_response(response):
            for req in reversed(self.requests):
                if req.url == response.url and req.status == 0:
                    req.status = response.status
                    req.response_headers = dict(response.headers)
                    req.duration_ms = round((time.time() - req.timestamp) * 1000, 2)
                    try:
                        req.response_body = response.text()[:5000]
                    except Exception:
                        req.response_body = ""
                    break

        page.on("request", on_request)
        page.on("response", on_response)

    def get_summary(self) -> dict:
        total = len(self.requests)
        by_status = {}
        by_type = {}
        total_duration = 0

        for r in self.requests:
            status_group = f"{r.status // 100}xx"
            by_status[status_group] = by_status.get(status_group, 0) + 1
            by_type[r.resource_type] = by_type.get(r.resource_type, 0) + 1
            total_duration += r.duration_ms

        failed = [r for r in self.requests if r.status >= 400]

        return {
            "total_requests": total,
            "by_status": by_status,
            "by_type": by_type,
            "total_duration_ms": round(total_duration, 2),
            "failed_requests": [
                {"url": r.url, "method": r.method, "status": r.status}
                for r in failed[:20]
            ],
        }

    def export_har(self, output_path: str | Path) -> str:
        """Export captured traffic as HAR (HTTP Archive) format."""
        har = {
            "log": {
                "version": "1.2",
                "creator": {"name": "QA Agent Network Capture", "version": "1.0"},
                "entries": [],
            }
        }

        for req in self.requests:
            entry = {
                "startedDateTime": time.strftime("%Y-%m-%dT%H:%M:%S.000Z",
                                                  time.gmtime(req.timestamp)),
                "time": req.duration_ms,
                "request": {
                    "method": req.method,
                    "url": req.url,
                    "headers": [{"name": k, "value": v}
                                for k, v in req.request_headers.items()],
                    "postData": {"text": req.request_body} if req.request_body else {},
                },
                "response": {
                    "status": req.status,
                    "headers": [{"name": k, "value": v}
                                for k, v in req.response_headers.items()],
                    "content": {
                        "size": len(req.response_body),
                        "text": req.response_body[:2000],
                    },
                },
                "timings": {"wait": req.duration_ms},
            }
            har["log"]["entries"].append(entry)

        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(har, indent=2), encoding="utf-8")
        logger.info("har_exported", path=str(path), entries=len(har["log"]["entries"]))
        return str(path)

    def find_api_calls(self, pattern: str = "/api/") -> list[CapturedRequest]:
        """Filter requests matching a URL pattern."""
        return [r for r in self.requests if pattern in r.url]

    def find_failed(self) -> list[CapturedRequest]:
        return [r for r in self.requests if r.status >= 400]
