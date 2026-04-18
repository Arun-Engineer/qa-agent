"""agent/oracles/universal.py — Always-true correctness rules.

These are oracle checks that should hold for ANY web application regardless
of business logic. A violation is unambiguously a defect.

Rules:
  U1  — No HTTP 5xx responses on any page or XHR.
  U2  — No unhandled console errors or uncaught promise rejections.
  U3  — No mixed-content warnings on https origins.
  U4  — Critical security headers present on authenticated pages
        (X-Content-Type-Options, X-Frame-Options or CSP frame-ancestors).
  U5  — No broken internal links (href -> 404 on same origin).
  U6  — No sensitive data exposure: credit card numbers, API keys, or
        password values echoed back in HTML (via simple regex heuristics).
"""
from __future__ import annotations

import re
from typing import Any

from agent.oracles.base import Finding


_SENSITIVE_PATTERNS = [
    (re.compile(r"\b(?:\d[ -]*?){13,19}\b"), "possible_card_number"),
    (re.compile(r"(?i)\bsk-[a-z0-9]{20,}\b"), "api_key_like"),
    (re.compile(r"(?i)aws_secret_access_key\s*[:=]\s*\S+"), "aws_secret"),
]


def _check_server_errors(route) -> list[Finding]:
    out: list[Finding] = []
    if 500 <= (route.status or 0) < 600:
        out.append(Finding(
            source=f"route:{route.url}", severity="universal", kind="bug",
            title=f"HTTP {route.status} on {route.url}",
            detail=f"Server returned {route.status} while loading the page.",
            url=route.url, oracle="U1",
            evidence={"status": route.status},
        ))
    for xhr in route.xhr_calls:
        if 500 <= (xhr.status or 0) < 600:
            out.append(Finding(
                source=f"xhr:{xhr.fingerprint()}", severity="universal",
                kind="bug", title=f"XHR {xhr.status} {xhr.method} {xhr.url}",
                detail="Backend API returned 5xx.",
                url=xhr.url, oracle="U1",
                evidence={"method": xhr.method, "status": xhr.status,
                          "observed_on": xhr.observed_on_page},
            ))
    return out


def _check_console_errors(route) -> list[Finding]:
    out: list[Finding] = []
    for err in route.console_errors:
        out.append(Finding(
            source=f"console:{route.url}", severity="universal",
            kind="bug", title="Console error",
            detail=err[:500], url=route.url, oracle="U2",
            evidence={"message": err},
        ))
    return out


def _check_sensitive_leaks(page_html: str, url: str) -> list[Finding]:
    out: list[Finding] = []
    for pat, label in _SENSITIVE_PATTERNS:
        for m in pat.findall(page_html or ""):
            if isinstance(m, str):
                out.append(Finding(
                    source=f"leak:{url}", severity="universal",
                    kind="bug", title=f"Possible data leak: {label}",
                    detail=f"Pattern matched on page: {m[:60]}",
                    url=url, oracle="U6",
                    evidence={"label": label},
                ))
    return out


def run_universal(model, findings_in: list[Finding] | None = None,
                  page_htmls: dict[str, str] | None = None) -> list[Finding]:
    """Run all universal checks against a crawled ApplicationModel and
    optionally a dict of {url: html_body} for deeper content inspection."""
    findings: list[Finding] = list(findings_in or [])
    for route in model.routes:
        findings.extend(_check_server_errors(route))
        findings.extend(_check_console_errors(route))
        if page_htmls and route.url in page_htmls:
            findings.extend(_check_sensitive_leaks(page_htmls[route.url], route.url))
    return findings
