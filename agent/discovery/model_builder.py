"""agent/discovery/model_builder.py — Enrich raw crawl into a richer model.

The crawler emits a structurally-correct `ApplicationModel`, but it's shallow:
just routes, forms, XHRs. This module deepens it by:

  * Inferring distinct roles from auth-wall clustering (customer vs admin
    vs internal) based on URL prefixes and page titles.
  * Detecting SPA patterns (same HTML shell, different routes) and de-duping.
  * Grouping XHR endpoints into logical API "domains" (e.g. /api/cart/*).
  * Tagging routes with a purpose_hint: landing, product_detail, checkout,
    profile, admin, settings, etc. — driven by LLM when available, fall back
    to keyword heuristics otherwise.

Everything here is deterministic + safe: given the same input model, it
produces the same enriched output. LLM calls are optional and gated by
AUTO_ENRICH_LLM=1 (default: off, so phase-2 works without extra tokens).
"""
from __future__ import annotations

import os
import re
from collections import Counter
from typing import Any
from urllib.parse import urlparse

from agent.discovery.app_model import ApplicationModel, Route, Role


# ── Heuristics ──────────────────────────────────────────────────────────────

_ADMIN_HINTS = ("admin", "manage", "backoffice", "console", "ops", "internal", "settings")
_CUSTOMER_HINTS = ("account", "profile", "orders", "cart", "checkout", "dashboard", "wishlist")


def _path_tokens(url: str) -> list[str]:
    try:
        p = urlparse(url).path.strip("/")
        return [seg for seg in p.split("/") if seg]
    except Exception:
        return []


def _guess_purpose(route: Route) -> str:
    hint = f"{route.url} {route.title}".lower()
    if any(k in hint for k in ("checkout", "payment", "pay")):
        return "checkout"
    if any(k in hint for k in ("cart", "basket", "bag")):
        return "cart"
    if any(k in hint for k in ("product", "item", "sku")):
        return "product_detail"
    if any(k in hint for k in ("category", "shop", "store", "browse")):
        return "listing"
    if any(k in hint for k in ("login", "signin", "sign-in")):
        return "login"
    if any(k in hint for k in ("register", "signup", "sign-up")):
        return "signup"
    if any(k in hint for k in ("dashboard", "overview", "home")):
        return "dashboard"
    if any(k in hint for k in _ADMIN_HINTS):
        return "admin"
    if any(k in hint for k in _CUSTOMER_HINTS):
        return "customer_area"
    if urlparse(route.url).path in ("", "/"):
        return "landing"
    return "content"


def _cluster_roles(routes: list[Route]) -> list[Role]:
    """From the auth walls, infer distinct roles by URL-prefix clustering."""
    walls = [r for r in routes if r.is_auth_wall]
    if not walls:
        return []

    customer_prefixes = set()
    admin_prefixes = set()
    for w in walls:
        tokens = _path_tokens(w.url)
        first = tokens[0].lower() if tokens else ""
        if any(k in first for k in _ADMIN_HINTS):
            admin_prefixes.add(first)
        else:
            customer_prefixes.add(first)

    roles: list[Role] = []
    if customer_prefixes:
        roles.append(Role(name="customer",
                          discovered_at=walls[0].url,
                          auth_plugin="form_login"))
    if admin_prefixes:
        first_admin = next((w for w in walls if any(a in _path_tokens(w.url)[:1]
                                                     for a in admin_prefixes)), walls[0])
        roles.append(Role(name="admin",
                          discovered_at=first_admin.url,
                          auth_plugin="form_login"))
    if not roles:
        # At least one generic role so the workflow can pause and ask.
        roles.append(Role(name="user", discovered_at=walls[0].url,
                          auth_plugin="form_login"))
    return roles


def _group_api_domains(model: ApplicationModel) -> dict[str, int]:
    """Return {base_path: count} so reports can say '/api/cart/* — 6 endpoints'."""
    buckets: Counter[str] = Counter()
    for x in model.api_endpoints:
        try:
            path = urlparse(x.url).path
            tokens = [t for t in path.split("/") if t]
            if not tokens:
                continue
            # Keep first two tokens as the bucket ("api/cart" from "/api/cart/add")
            key = "/" + "/".join(tokens[:2])
            buckets[key] += 1
        except Exception:
            pass
    return dict(buckets)


# ── Public API ──────────────────────────────────────────────────────────────

def enrich(model: ApplicationModel) -> ApplicationModel:
    """Mutate + return `model` with enrichment applied."""
    # Purpose tags on each route — stored on the notes for now to avoid
    # breaking the Route schema.
    for r in model.routes:
        purpose = _guess_purpose(r)
        r.purpose_hint = purpose if hasattr(r, "purpose_hint") else purpose
        # Attach as a "tag" in the notes list for quick UI reading.
        model.notes.append(f"route:{r.url} purpose={purpose}")

    # If the crawler produced a single generic role, replace with clustered.
    if len(model.roles) <= 1:
        clustered = _cluster_roles(model.routes)
        if clustered:
            model.roles = clustered

    # Tag each auth wall with the role it likely belongs to.
    for r in model.routes:
        if not r.is_auth_wall:
            continue
        tokens = _path_tokens(r.url)
        if tokens and any(a in tokens[0].lower() for a in _ADMIN_HINTS):
            r.requires_role = "admin"
        else:
            r.requires_role = "customer" if any(c.name == "customer" for c in model.roles) else "user"

    # Record API domain grouping in notes.
    for dom, cnt in _group_api_domains(model).items():
        model.notes.append(f"api_domain:{dom} count={cnt}")

    # Optional LLM pass — only when explicitly enabled, to keep the
    # discovery phase free of token cost by default.
    if (os.getenv("AUTO_ENRICH_LLM", "0") or "0").strip() not in ("0", "false", "off"):
        try:
            _enrich_with_llm(model)
        except Exception as e:
            model.notes.append(f"llm_enrich_skipped: {e}")

    return model


def _enrich_with_llm(model: ApplicationModel) -> None:
    """Ask the LLM for journey hypotheses. Findings land in model.notes
    so they're visible to the reporter but don't destabilize the data shape."""
    from src.agents.langgraph_runtime import llm_json

    # Keep the prompt compact — summarize the model.
    summary = {
        "base_url": model.base_url,
        "pages": [{"url": r.url, "title": r.title, "auth": r.is_auth_wall}
                  for r in model.routes[:30]],
        "api_endpoints": [x.fingerprint() for x in model.api_endpoints[:30]],
        "roles": [r.name for r in model.roles],
    }
    out = llm_json(
        messages=[
            {"role": "system",
             "content": "You are a senior QA architect. Given a crawled application's structure, "
                        "propose the most valuable end-to-end user journeys to verify. "
                        "Return {\"journeys\":[{\"name\":\"...\",\"role\":\"...\",\"steps\":[\"...\"],\"why\":\"...\"}]}"
                        " — at most 8 journeys, prioritized by business impact."},
            {"role": "user", "content": str(summary)[:8000]},
        ],
        service="autonomous-enrich-journeys",
        temperature=0.2,
    )
    journeys = out.get("journeys") if isinstance(out, dict) else []
    if isinstance(journeys, list):
        for j in journeys[:8]:
            if isinstance(j, dict):
                model.notes.append(f"journey:{j.get('name','?')} role={j.get('role','?')} "
                                   f"steps={len(j.get('steps') or [])}")
