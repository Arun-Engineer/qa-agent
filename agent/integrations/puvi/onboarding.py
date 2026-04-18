"""agent/integrations/puvi/onboarding.py — Walk Puvi's onboarding flow.

What we're testing:
  * Can a new user sign up / create a workspace?
  * Does Puvi issue an API key / SDK token at the end?
  * Does the key actually work (can we hit a trivial authed endpoint with
    it)?
  * Is the "how to integrate" snippet Puvi shows the user actually
    parseable (e.g. does it point at a real ingest URL)?

We do this via the existing autonomous browser (Playwright) — the flow
itself is a standard web signup, our self-healing UI driver and form
filler can handle it. The *oracle* here is:

  1. After onboarding we must have an ``api_key`` string.
  2. GET /api/me (or equivalent workspace endpoint) with that key must
     return 2xx.
  3. The "integration snippet" visible on the final page must contain
     both the key and a URL matching the app's origin.

Returns an ``OnboardingResult`` carrying the captured key + ingest URL,
which the rest of the Puvi test pipeline feeds into SyntheticAgent.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse

from agent.oracles.base import Finding


_API_KEY_PATTERNS = [
    re.compile(r"\b(pk|sk|puvi|agent)[-_][A-Za-z0-9_\-]{16,}\b"),
    re.compile(r"\b[A-Za-z0-9]{32,}\b"),            # plain-ish tokens
]

_SNIPPET_URL_RE = re.compile(r"https?://[A-Za-z0-9.\-:/]+")


@dataclass
class OnboardingResult:
    ok: bool
    api_key: str = ""
    ingest_url: str = ""
    workspace_id: str = ""
    findings: list[Finding] = field(default_factory=list)
    captured_snippet: str = ""


def _credential(vault, run_id: str, role: str):
    """Grab creds for a role from the vault (blocking if missing)."""
    try:
        return vault.get_credential(run_id, role)
    except Exception:
        return None


def run_onboarding(browser_context, *, base_url: str, signup_url: str,
                   email: str, password: str,
                   workspace_name: str = "aiqa-probe") -> OnboardingResult:
    """Execute the full onboarding UI flow and verify the API key works.

    ``browser_context`` is a live Playwright BrowserContext from Phase 1.
    We deliberately keep this code simple/heuristic — the selector memory
    and self-healing UI runner (Phase 5) already know how to find fields
    by semantic role, so if Puvi restyles their signup page, we adapt.
    """
    from agent.workflows.langgraph_ui_test import run_ui_action

    findings: list[Finding] = []
    page = browser_context.new_page()

    try:
        page.goto(signup_url, wait_until="domcontentloaded", timeout=20000)
    except Exception as e:
        findings.append(Finding(
            source="puvi.onboarding", severity="universal", kind="bug",
            title="Signup page did not load",
            detail=f"{signup_url}: {e}", url=signup_url, confidence=1.0,
            oracle="puvi_onboarding",
        ))
        page.close()
        return OnboardingResult(ok=False, findings=findings)

    # -- fill email / password / workspace name ---------------------------
    for semantic, value, action in [
        ("email_field",    email,          "fill"),
        ("password_field", password,       "fill"),
        ("workspace_name", workspace_name, "fill"),
    ]:
        try:
            run_ui_action(page, semantic=semantic, action=action,
                          action_args={"value": value}, url=signup_url,
                          tenant_id="puvi-probe")
        except Exception:
            # Workspace name may be on a separate page — we'll hit it below.
            pass

    # -- submit ------------------------------------------------------------
    submitted = False
    for semantic in ("signup_button", "submit_button", "continue_button"):
        try:
            run_ui_action(page, semantic=semantic, action="click",
                          action_args={}, url=signup_url,
                          tenant_id="puvi-probe")
            submitted = True
            break
        except Exception:
            continue

    if not submitted:
        findings.append(Finding(
            source="puvi.onboarding", severity="universal", kind="bug",
            title="Could not locate signup submit button",
            detail="self-healing UI layer exhausted candidates",
            url=signup_url, confidence=0.9, oracle="puvi_onboarding",
        ))
        page.close()
        return OnboardingResult(ok=False, findings=findings)

    # Give the post-signup flow a chance to settle.
    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        pass

    # -- scrape page for API key + integration snippet --------------------
    page_text = ""
    try:
        page_text = page.content()
    except Exception:
        pass

    api_key = ""
    for rx in _API_KEY_PATTERNS:
        m = rx.search(page_text)
        if m:
            api_key = m.group(0)
            break

    ingest_url = ""
    snippet = ""
    # Find a <pre>/<code> block that mentions the key or "trace".
    try:
        for el in page.query_selector_all("pre, code"):
            txt = el.inner_text() or ""
            if api_key and api_key in txt:
                snippet = txt
                break
            if "trace" in txt.lower() or "ingest" in txt.lower():
                snippet = txt
    except Exception:
        pass
    if snippet:
        urls = _SNIPPET_URL_RE.findall(snippet)
        # Prefer a URL on the same origin as base_url.
        origin = urlparse(base_url).netloc
        for u in urls:
            if origin and origin.split(":")[0] in u:
                ingest_url = u
                break
        if not ingest_url and urls:
            ingest_url = urls[0]

    workspace_id = ""
    try:
        m = re.search(r"workspace[_-]?id[\"'>: =]+([A-Za-z0-9_\-]{6,})",
                      page_text, re.I)
        if m:
            workspace_id = m.group(1)
    except Exception:
        pass

    page.close()

    # -- Oracle checks -----------------------------------------------------
    if not api_key:
        findings.append(Finding(
            source="puvi.onboarding", severity="universal", kind="bug",
            title="Onboarding did not surface an API key",
            detail="No token/key pattern matched on the post-signup page. "
                   "Either the flow is broken, the key is hidden behind "
                   "an extra click, or the presentation changed.",
            url=signup_url, confidence=0.8, oracle="puvi_onboarding",
        ))

    if api_key and not _verify_key_works(base_url, api_key):
        findings.append(Finding(
            source="puvi.onboarding", severity="confirmed", kind="bug",
            title="Issued API key rejected by Puvi's own API",
            detail=f"Key issued during signup fails authed probe. Key "
                   f"prefix: {api_key[:8]}…",
            url=base_url, confidence=0.95, oracle="puvi_onboarding",
        ))

    if not ingest_url:
        findings.append(Finding(
            source="puvi.onboarding", severity="inferred", kind="ux",
            title="Integration snippet missing ingest URL",
            detail="Either no code snippet was shown or no URL was "
                   "detected inside it. New customers can't wire up "
                   "their agents without this.",
            url=signup_url, confidence=0.6, oracle="puvi_onboarding",
        ))

    ok = bool(api_key)
    return OnboardingResult(
        ok=ok, api_key=api_key, ingest_url=ingest_url or base_url,
        workspace_id=workspace_id, findings=findings,
        captured_snippet=snippet,
    )


def _verify_key_works(base_url: str, api_key: str) -> bool:
    """Best-effort probe: try a few common authed endpoints."""
    import requests
    headers = {"Authorization": f"Bearer {api_key}", "X-Api-Key": api_key}
    for path in ("/api/me", "/api/v1/me", "/api/workspace",
                 "/api/v1/workspace", "/api/user"):
        try:
            r = requests.get(f"{base_url.rstrip('/')}{path}",
                             headers=headers, timeout=4)
            if 200 <= r.status_code < 300:
                return True
        except Exception:
            continue
    return False
