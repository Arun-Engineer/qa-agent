"""agent/discovery/workflow_inference.py — Propose a test suite from a model.

Inputs:  an enriched `ApplicationModel`.
Outputs: a list of test step dicts the executor can run, categorized into:

    smoke           — no auth, every public page should not 5xx
    api_contract    — every observed XHR, replayed, expect original status class
    auth_flow       — role login → reach protected landing → ping a few links
    journey         — multi-step flows derived from the model (Phase 4 deepens)
    visual_baseline — screenshot targets (consumed by Phase 6 baselining)

Every inferred step carries a `confidence` score (0.0-1.0) and a `rationale`
so the UI can show *why* each test exists. Low-confidence steps can be
filtered out when the user wants a fast smoke instead of full coverage.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import urljoin, urlparse

from agent.discovery.app_model import ApplicationModel, Route


def _priority(route: Route) -> int:
    """Lower = more important. Landing + checkout + cart rank highest."""
    hint = f"{route.url} {route.title}".lower()
    if urlparse(route.url).path in ("", "/"):
        return 0
    if any(k in hint for k in ("checkout", "cart", "payment")):
        return 1
    if any(k in hint for k in ("login", "signup", "register")):
        return 2
    if route.is_auth_wall:
        return 3
    return 5


def propose_suite(model: ApplicationModel, *,
                  include_visual: bool = True,
                  max_api: int = 40,
                  max_smoke: int = 30) -> list[dict]:
    """Return a list of step dicts. See module docstring for kinds."""
    steps: list[dict] = []

    # ── Smoke: each public page returns non-5xx ─────────────────────────
    public = sorted(model.public_routes(), key=_priority)[:max_smoke]
    for route in public:
        steps.append({
            "kind": "smoke.page_loads",
            "url": route.url,
            "expect_status_max": 499,
            "confidence": 0.95,
            "rationale": "Every public route must not 5xx — universal oracle.",
        })

    # ── API contract: every observed XHR replay ─────────────────────────
    for xhr in model.api_endpoints[:max_api]:
        status_class = (xhr.status // 100) if xhr.status else 2
        steps.append({
            "kind": "api.contract_replay",
            "method": xhr.method,
            "url": xhr.url,
            "expect_status_class": status_class or 2,
            "confidence": 0.9,
            "rationale": f"Observed during crawl with status {xhr.status or '2xx'}; replay must match class.",
        })

    # ── Auth flows: one login smoke per role ────────────────────────────
    for role in model.roles:
        landing = role.discovered_at or model.base_url
        steps.append({
            "kind": "auth.login_smoke",
            "role": role.name,
            "plugin": role.auth_plugin,
            "landing_url": landing,
            "confidence": 0.85,
            "rationale": f"Verify {role.name} can authenticate and reach {landing}.",
        })

    # ── Journey: multi-step flows (anonymous + per-role) ────────────────
    # Pick the top N public pages and chain them as a "happy path" walk.
    walk = [r.url for r in public[:5]]
    if len(walk) >= 2:
        steps.append({
            "kind": "journey.walk",
            "name": "anonymous_happy_path",
            "urls": walk,
            "role": "anonymous",
            "confidence": 0.6,
            "rationale": "Walk the top public pages in priority order; catches cross-page regressions.",
        })
    for role in model.roles:
        post_login = [r.url for r in model.routes
                      if r.is_auth_wall and r.requires_role in (role.name, "")][:3]
        if post_login:
            steps.append({
                "kind": "journey.walk",
                "name": f"{role.name}_post_login_walk",
                "urls": post_login,
                "role": role.name,
                "confidence": 0.55,
                "rationale": f"Verify authenticated {role.name} pages load after login.",
            })

    # ── Visual baseline: screenshot key landing pages ───────────────────
    if include_visual:
        for route in public[:5]:
            steps.append({
                "kind": "visual.baseline",
                "url": route.url,
                "confidence": 0.7,
                "rationale": "Key landing page — snapshot for regression diffing.",
            })

    return steps


def summarize_suite(steps: list[dict]) -> dict[str, Any]:
    """Small summary for the UI card."""
    by_kind: dict[str, int] = {}
    for s in steps:
        by_kind[s["kind"]] = by_kind.get(s["kind"], 0) + 1
    return {
        "total": len(steps),
        "by_kind": by_kind,
        "avg_confidence": round(
            sum(s.get("confidence", 0) for s in steps) / max(1, len(steps)), 2
        ),
    }
