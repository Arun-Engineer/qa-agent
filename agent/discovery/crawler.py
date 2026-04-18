"""agent/discovery/crawler.py — Bounded Playwright discovery crawler.

Given a URL, walks the site breadth-first (respecting budgets), records:
  - Route graph (pages + links between them)
  - Forms (with field shapes + login-wall detection)
  - XHR / fetch calls via network interception
  - HTTP status codes, redirects, 4xx/5xx hotspots
  - Console errors

Returns an `ApplicationModel`.

This is the Phase 2 keystone. It's intentionally minimal for the first cut —
enough to detect login walls (so Phase 1's credential-prompt flow works) and
enumerate public routes. Smarter heuristics (SPA router awareness, OAuth
redirect tracing, authenticated re-crawls) arrive in later phases.

Budgets (env-overridable):
  AUTO_MAX_PAGES   = 30
  AUTO_MAX_DEPTH   = 3
  AUTO_PAGE_TIMEOUT_S = 20

All Playwright calls are in a sync context running in a worker thread so the
caller can `await asyncio.to_thread(crawl, ...)`.
"""
from __future__ import annotations

import os
import re
import time
from typing import Any, Callable, Optional
from urllib.parse import urljoin, urlparse

from agent.discovery.app_model import (
    ApplicationModel, Route, Form, FormField, XhrCall,
)


# ── Helpers ─────────────────────────────────────────────────────────────────

def _origin(url: str) -> str:
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}"


def _is_same_origin(a: str, b: str) -> bool:
    return _origin(a) == _origin(b)


def _infer_field_type(raw_type: str, name: str) -> str:
    rt = (raw_type or "").lower()
    known = {"text", "email", "password", "tel", "number", "url", "search",
             "checkbox", "radio", "file", "hidden", "submit", "date"}
    if rt in known:
        return rt
    if rt == "select-one":
        return "select"
    lname = (name or "").lower()
    if "email" in lname:
        return "email"
    if "pass" in lname:
        return "password"
    if "phone" in lname or "mobile" in lname:
        return "tel"
    return "text"


def _looks_like_auth_wall(url: str, title: str, forms: list[Form]) -> bool:
    hint = f"{url} {title}".lower()
    keywords = ("login", "sign in", "signin", "sign-in", "log in", "log-in",
                "authenticate", "unauthorized")
    if any(k in hint for k in keywords):
        return True
    return any(f.looks_like_login() for f in forms)


# ── Main entry point ───────────────────────────────────────────────────────

def crawl(
    url: str,
    *,
    max_pages: Optional[int] = None,
    max_depth: Optional[int] = None,
    on_event: Optional[Callable[[dict], None]] = None,
) -> ApplicationModel:
    """Walk the site starting at `url`. Returns an ApplicationModel.

    on_event: optional callback that receives dicts like
        {"kind": "page_done", "url": "...", "status": 200, "forms": 2}
    Useful for SSE progress streams.
    """
    max_pages = int(max_pages or os.getenv("AUTO_MAX_PAGES", "30"))
    max_depth = int(max_depth or os.getenv("AUTO_MAX_DEPTH", "3"))
    page_timeout_ms = int(float(os.getenv("AUTO_PAGE_TIMEOUT_S", "20")) * 1000)

    def emit(evt: dict) -> None:
        if on_event:
            try:
                on_event(evt)
            except Exception:
                pass

    # Lazy import so the rest of the codebase still loads if playwright is
    # somehow missing. We import here so the import error surfaces at crawl
    # time with a clear message instead of at module import time.
    try:
        from playwright.sync_api import sync_playwright  # type: ignore
    except Exception as e:
        raise RuntimeError(
            "Playwright not installed. Run: pip install playwright && "
            "python -m playwright install chromium"
        ) from e

    model = ApplicationModel(base_url=url)
    visited: set[str] = set()
    queue: list[tuple[str, int]] = [(url, 0)]

    # XHR observations grouped by page
    current_page_xhrs: list[XhrCall] = []
    # Cross-page de-dup of API endpoints by fingerprint
    api_seen: set[str] = set()

    started = time.time()
    emit({"kind": "crawl_start", "url": url, "max_pages": max_pages, "max_depth": max_depth})

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(
            ignore_https_errors=True,
            user_agent=os.getenv("AUTO_USER_AGENT",
                                 "Mozilla/5.0 (QAAgent) Autonomous-Discovery/1.0"),
        )

        while queue and len(visited) < max_pages:
            cur_url, depth = queue.pop(0)
            if cur_url in visited:
                continue
            visited.add(cur_url)

            page = context.new_page()

            current_page_xhrs = []

            def _on_response(resp, _bucket=current_page_xhrs):
                try:
                    r_url = resp.url
                    # Skip the main document and static assets; keep API calls.
                    ct = (resp.headers.get("content-type") or "").lower()
                    if not any(k in ct for k in ("json", "xml", "text/plain")):
                        return
                    if r_url.endswith((".html", ".htm")):
                        return
                    _bucket.append(XhrCall(
                        method=resp.request.method,
                        url=r_url,
                        status=resp.status,
                        response_content_type=ct,
                        observed_on_page=cur_url,
                    ))
                except Exception:
                    pass

            page.on("response", _on_response)

            console_errs: list[str] = []
            page.on("console", lambda msg: (
                console_errs.append(msg.text) if msg.type == "error" else None
            ))

            status_code = 0
            try:
                resp = page.goto(cur_url, wait_until="domcontentloaded",
                                 timeout=page_timeout_ms)
                status_code = resp.status if resp else 0
                # Let a bit of JS run so SPA routes render + XHRs fire.
                page.wait_for_load_state("networkidle", timeout=5000)
            except Exception as e:
                emit({"kind": "page_error", "url": cur_url, "error": str(e)})
                try:
                    page.close()
                except Exception:
                    pass
                continue

            title = ""
            try:
                title = page.title() or ""
            except Exception:
                pass

            # ── Forms ──
            forms: list[Form] = []
            try:
                raw_forms = page.evaluate("""
                    () => Array.from(document.forms).map(f => ({
                        action: f.action || '',
                        method: (f.method || 'GET').toUpperCase(),
                        fields: Array.from(f.elements)
                            .filter(e => e.name || e.id)
                            .map(e => ({
                                name: e.name || e.id || '',
                                type: (e.type || 'text'),
                                required: !!e.required,
                                placeholder: e.placeholder || '',
                                label: (
                                    (e.labels && e.labels[0] && e.labels[0].innerText) ||
                                    e.getAttribute('aria-label') || ''
                                ),
                            }))
                    }))
                """)
                for i, f in enumerate(raw_forms or []):
                    fields = [
                        FormField(
                            name=ff.get("name", ""),
                            type=_infer_field_type(ff.get("type", ""), ff.get("name", "")),
                            required=bool(ff.get("required")),
                            placeholder=ff.get("placeholder", ""),
                            label=ff.get("label", ""),
                        )
                        for ff in f.get("fields", [])
                    ]
                    form = Form(
                        selector=f"form:nth-of-type({i+1})",
                        action=f.get("action", ""),
                        method=f.get("method", "POST"),
                        fields=fields,
                    )
                    if form.looks_like_login():
                        form.purpose_hint = "login"
                    forms.append(form)
            except Exception:
                pass

            # ── Links ──
            links: list[str] = []
            try:
                raw_links = page.evaluate("""
                    () => Array.from(document.querySelectorAll('a[href]'))
                        .map(a => a.href).filter(Boolean)
                """) or []
                for href in raw_links:
                    abs_url = urljoin(cur_url, href).split("#")[0]
                    if _is_same_origin(abs_url, url) and abs_url not in visited:
                        links.append(abs_url)
            except Exception:
                pass

            is_wall = _looks_like_auth_wall(cur_url, title, forms) or status_code in (401, 403)

            route = Route(
                url=cur_url,
                title=title,
                status=status_code,
                forms=forms,
                xhr_calls=list(current_page_xhrs),
                depth=depth,
                is_auth_wall=is_wall,
                links_to=list(dict.fromkeys(links))[:50],
                console_errors=console_errs[:20],
            )
            model.routes.append(route)

            # Merge API endpoints into the global list (dedup by fingerprint).
            for x in current_page_xhrs:
                fp = x.fingerprint()
                if fp not in api_seen:
                    api_seen.add(fp)
                    model.api_endpoints.append(x)

            emit({
                "kind": "page_done",
                "url": cur_url,
                "status": status_code,
                "title": title,
                "forms": len(forms),
                "xhrs": len(current_page_xhrs),
                "auth_wall": is_wall,
                "visited": len(visited),
            })

            try:
                page.close()
            except Exception:
                pass

            # Enqueue next links unless we've hit the depth ceiling.
            if depth + 1 <= max_depth:
                for lk in route.links_to:
                    if lk not in visited and not any(lk == q[0] for q in queue):
                        queue.append((lk, depth + 1))

        try:
            context.close()
            browser.close()
        except Exception:
            pass

    # ── Derive roles ──
    if model.auth_walls():
        # Minimum viable: one "user" role we'll ask creds for. Later phases
        # will differentiate customer vs admin from crawling patterns.
        model.roles.append(
            Role_default("user", discovered_at=model.auth_walls()[0].url)
        )

    model.title = next((r.title for r in model.routes if r.title), "")
    model.discovery_budget_used = {
        "pages": len(visited),
        "max_pages": max_pages,
        "max_depth": max_depth,
        "elapsed_s": int(time.time() - started),
    }
    emit({"kind": "crawl_done", "pages": len(model.routes),
          "auth_walls": len(model.auth_walls()),
          "api_endpoints": len(model.api_endpoints),
          "elapsed_s": model.discovery_budget_used["elapsed_s"]})
    return model


# Small helper wrapping Role() with a default auth plugin, kept private here
# to avoid a circular import at module-load time.
def Role_default(name: str, **kw):
    from agent.discovery.app_model import Role
    return Role(name=name, auth_plugin="form_login", **kw)
