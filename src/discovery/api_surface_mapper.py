"""API Surface Mapper — Playwright CDP network interception to capture XHR/Fetch calls.

Listens to all network requests during crawling and builds an API endpoint catalog.
Filters out static assets and tracking pixels, captures method/url/status/timing.
"""
from __future__ import annotations

import re
import os
import yaml
import time
import structlog
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from src.discovery.site_model import ApiEndpoint

logger = structlog.get_logger()


def _load_ignore_patterns() -> list[re.Pattern]:
    """Load URL patterns to ignore from config."""
    defaults = [
        r".*\.png$", r".*\.jpg$", r".*\.jpeg$", r".*\.gif$",
        r".*\.css$", r".*\.js$", r".*\.woff2?$", r".*\.ttf$",
        r".*\.svg$", r".*\.ico$", r".*\.map$",
        r".*google-analytics\.com.*", r".*googletagmanager\.com.*",
        r".*facebook\.com.*", r".*doubleclick\.net.*",
        r".*hotjar\.com.*", r".*segment\.com.*",
        r".*sentry\.io.*", r".*newrelic\.com.*",
        r".*cdn\..*\.com.*\.js$",
    ]

    try:
        config_path = Path("config/discovery.yaml")
        if config_path.exists():
            with open(config_path) as f:
                data = yaml.safe_load(f)
            custom = data.get("api_capture", {}).get("ignore_patterns", [])
            if custom:
                defaults = custom
    except Exception:
        pass

    return [re.compile(p, re.IGNORECASE) for p in defaults]


# Module-level cache
_IGNORE_PATTERNS: list[re.Pattern] = []


def _get_ignore_patterns() -> list[re.Pattern]:
    global _IGNORE_PATTERNS
    if not _IGNORE_PATTERNS:
        _IGNORE_PATTERNS = _load_ignore_patterns()
    return _IGNORE_PATTERNS


def _should_ignore(url: str) -> bool:
    """Check if a URL matches any ignore pattern."""
    for pattern in _get_ignore_patterns():
        if pattern.search(url):
            return True
    return False


def _is_api_call(url: str, resource_type: str) -> bool:
    """Determine if a network request is an API call (XHR/Fetch)."""
    if resource_type in ("xhr", "fetch"):
        return True

    # Heuristic: JSON API endpoints
    parsed = urlparse(url)
    path = parsed.path.lower()

    api_indicators = ["/api/", "/v1/", "/v2/", "/v3/", "/graphql", "/rest/", "/rpc/"]
    if any(ind in path for ind in api_indicators):
        return True

    # Check for JSON content type in URL (rare but happens)
    if "json" in parsed.query.lower():
        return True

    return False


class ApiSurfaceMapper:
    """Intercepts network requests during page navigation to build API catalog.

    Usage:
        mapper = ApiSurfaceMapper(page)
        mapper.start()
        # ... navigate pages ...
        endpoints = mapper.stop()
    """

    def __init__(self, page, base_url: str = ""):
        self._page = page
        self._base_url = base_url
        self._endpoints: list[ApiEndpoint] = []
        self._seen_urls: set[str] = set()  # dedup (method + url)
        self._request_timings: dict[str, float] = {}
        self._listening = False
        self._current_page_url = ""

    def start(self):
        """Start intercepting network requests."""
        if self._listening:
            return

        self._page.on("request", self._on_request)
        self._page.on("response", self._on_response)
        self._listening = True
        logger.info("api_mapper_started")

    def stop(self) -> list[ApiEndpoint]:
        """Stop intercepting and return captured endpoints."""
        if self._listening:
            try:
                self._page.remove_listener("request", self._on_request)
                self._page.remove_listener("response", self._on_response)
            except Exception:
                pass
            self._listening = False

        logger.info("api_mapper_stopped", total_endpoints=len(self._endpoints))
        return list(self._endpoints)

    def set_current_page(self, url: str):
        """Track which page triggered the API calls."""
        self._current_page_url = url

    def _on_request(self, request):
        """Handle outgoing request."""
        try:
            url = request.url
            resource_type = request.resource_type
            method = request.method.upper()

            if _should_ignore(url):
                return

            if not _is_api_call(url, resource_type):
                return

            dedup_key = f"{method}:{url}"
            if dedup_key in self._seen_urls:
                return

            self._request_timings[dedup_key] = time.time()

        except Exception as e:
            logger.debug("api_mapper_request_error", error=str(e))

    def _on_response(self, response):
        """Handle incoming response."""
        try:
            request = response.request
            url = request.url
            method = request.method.upper()
            resource_type = request.resource_type

            if _should_ignore(url):
                return

            if not _is_api_call(url, resource_type):
                return

            dedup_key = f"{method}:{url}"
            if dedup_key in self._seen_urls:
                return
            self._seen_urls.add(dedup_key)

            # Timing
            timing_ms = None
            if dedup_key in self._request_timings:
                timing_ms = round((time.time() - self._request_timings.pop(dedup_key)) * 1000, 2)

            parsed = urlparse(url)
            content_type = ""
            response_headers = {}
            try:
                headers = response.headers
                content_type = headers.get("content-type", "")
                # Capture select response headers
                for h in ("content-type", "x-request-id", "x-correlation-id",
                          "cache-control", "x-ratelimit-remaining"):
                    if h in headers:
                        response_headers[h] = headers[h]
            except Exception:
                pass

            request_headers = {}
            try:
                req_headers = request.headers
                for h in ("authorization", "content-type", "accept", "x-api-key",
                          "x-requested-with", "origin"):
                    val = req_headers.get(h, "")
                    if val:
                        # Mask sensitive values
                        if h in ("authorization", "x-api-key"):
                            request_headers[h] = val[:10] + "..."
                        else:
                            request_headers[h] = val
            except Exception:
                pass

            endpoint = ApiEndpoint(
                method=method,
                url=url,
                path=parsed.path,
                status_code=response.status,
                content_type=content_type,
                request_headers=request_headers,
                response_headers=response_headers,
                triggered_from=self._current_page_url,
                timing_ms=timing_ms,
            )
            self._endpoints.append(endpoint)

            logger.debug("api_captured", method=method, path=parsed.path,
                          status=response.status, timing_ms=timing_ms)

        except Exception as e:
            logger.debug("api_mapper_response_error", error=str(e))

    @property
    def captured_count(self) -> int:
        return len(self._endpoints)

    def get_summary(self) -> dict:
        """Return summary of captured API surface."""
        by_method: dict[str, int] = {}
        by_status: dict[str, int] = {}
        unique_paths: set[str] = set()

        for ep in self._endpoints:
            by_method[ep.method] = by_method.get(ep.method, 0) + 1
            status_group = f"{ep.status_code // 100}xx" if ep.status_code else "unknown"
            by_status[status_group] = by_status.get(status_group, 0) + 1
            unique_paths.add(ep.path)

        return {
            "total_captured": len(self._endpoints),
            "unique_paths": len(unique_paths),
            "by_method": by_method,
            "by_status": by_status,
        }
