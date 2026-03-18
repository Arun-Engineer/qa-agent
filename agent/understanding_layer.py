# agent/understanding_layer.py  [final-fix]
"""
Key fix: when recon finds only a login wall, inject the text
"LOGIN WALL DETECTED" into the enriched spec so planner.py
_detect_login_wall() fires correctly — no context chain needed.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Optional, Tuple, List
from urllib.parse import urlparse

URL_RE = re.compile(r"https?://[^\s\)\"\']+")

# Signals that indicate app REQUIRES login to access (login wall)
# NOT signals that the spec is ABOUT testing login functionality
_LOGIN_WALL_SIGNALS = [
    "login required", "sign in required", "must login",
    "mobile:", "otp:", "use this login", "credentials if required", "forgot password",
]
_NON_LOGIN_KEYWORDS = [
    "cart", "product", "checkout", "category", "search",
    "dashboard", "home", "profile", "order", "menu",
]


def _first_url(text: str) -> Optional[str]:
    m = URL_RE.search(text or "")
    return m.group(0).rstrip(").,;\"'") if m else None


def _normalize_base_url(url: str) -> Optional[str]:
    if not url:
        return None
    p = urlparse(url)
    if not p.scheme or not p.netloc:
        return None
    return f"{p.scheme}://{p.netloc}".rstrip("/")


def _load_recon_runner():
    try:
        from agent.tools.ui_recon_runner import run_recon  # type: ignore
        return run_recon
    except Exception:
        try:
            from agent.ui_recon_runner import run_recon  # type: ignore
            return run_recon
        except Exception:
            return None


def _detect_login_wall(recon: dict) -> bool:
    """True if recon only found a login/auth gate and nothing beyond it."""
    pages = recon.get("pages_crawled") or 0
    summary = (recon.get("summary") or "").lower()
    if pages < 2:
        return True
    login_hits = sum(1 for kw in _LOGIN_WALL_SIGNALS if kw in summary)
    non_login_hits = sum(1 for kw in _NON_LOGIN_KEYWORDS if kw in summary)
    return login_hits >= 2 and non_login_hits == 0


def _extract_user_scenarios(spec: str) -> List[str]:
    out = []
    for line in spec.splitlines():
        s = line.strip()
        if re.match(r"^\d+[\.\)]\s+\S", s):
            out.append(s)
        elif re.match(r"^[-*•]\s+\S", s):
            out.append(s)
    return out


@dataclass
class UnderstandingContext:
    base_url: Optional[str] = None
    recon_status: Optional[str] = None
    site_model_path: Optional[str] = None
    recon_summary: str = ""
    recon_pages_crawled: Optional[int] = None
    login_wall_detected: bool = False
    user_scenarios: List[str] = field(default_factory=list)


def enrich_spec_with_understanding(
    spec: str,
    max_pages: int = 25,
    max_depth: int = 2,
) -> Tuple[str, UnderstandingContext]:
    ctx = UnderstandingContext()
    spec = (spec or "").strip()
    ctx.user_scenarios = _extract_user_scenarios(spec)

    # Resolve base URL
    url_in_spec = _first_url(spec)
    base_url = _normalize_base_url(url_in_spec or "")
    if not base_url:
        base_url = _normalize_base_url(
            os.getenv("APP_BASE_URL") or os.getenv("BASE_URL") or ""
        )
    ctx.base_url = base_url

    if base_url:
        os.environ["APP_BASE_URL"] = base_url
        os.environ["BASE_URL"] = base_url

    if not base_url:
        return _build_enriched_spec(spec, ctx), ctx

    if os.getenv("QA_DISABLE_RECON", "").strip() in ("1", "true", "TRUE", "yes", "YES"):
        return _build_enriched_spec(spec, ctx), ctx

    run_recon = _load_recon_runner()
    if not run_recon:
        return _build_enriched_spec(spec, ctx), ctx

    try:
        recon = run_recon(base_url=base_url, max_pages=max_pages, max_depth=max_depth)
    except Exception as e:
        ctx.recon_status = "error"
        ctx.recon_summary = f"Recon failed: {e}"
        return _build_enriched_spec(spec, ctx), ctx

    ctx.recon_status = recon.get("status")
    ctx.site_model_path = recon.get("model_path")
    ctx.recon_summary = (recon.get("summary") or "").strip()
    ctx.recon_pages_crawled = recon.get("pages_crawled")
    # If spec is ABOUT testing login functionality, it is NOT a login wall
    _spec_lower = spec.lower()
    _is_testing_login = any(x in _spec_lower for x in [
        'invalid login', 'test login', 'login cases', 'login functionality',
        'login test', 'invalid credential', 'wrong password', 'wrong username',
        'login for', 'login with'
    ])
    ctx.login_wall_detected = False if _is_testing_login else _detect_login_wall(recon)

    if ctx.site_model_path:
        os.environ["SITE_MODEL_PATH"] = ctx.site_model_path

    return _build_enriched_spec(spec, ctx), ctx


def _build_enriched_spec(spec: str, ctx: UnderstandingContext) -> str:
    """
    CRITICAL: inject "LOGIN WALL DETECTED" as literal text into the
    enriched spec when detected. planner._detect_login_wall() scans
    spec text directly — this is the bridge between the two modules.
    """
    lines = [spec]

    if ctx.user_scenarios:
        n = len(ctx.user_scenarios)
        lines.append("\n\n" + "=" * 60)
        lines.append(f"USER SPEC: {n} EXPLICIT TEST SCENARIOS (AUTHORITATIVE)")
        lines.append("=" * 60)
        lines.append(f"The user defined {n} scenarios. Generate ONE step per scenario.")
        lines.append("DO NOT collapse, skip, or merge them.")

    if not ctx.base_url:
        return "\n".join(lines)

    lines.append("\n\n" + "=" * 60)
    lines.append("SITE CONTEXT (advisory — use for selectors only, NOT to decide what to test)")
    lines.append("=" * 60)
    lines.append(f"BASE_URL: {ctx.base_url}")
    lines.append(f"RECON_STATUS: {ctx.recon_status}")
    lines.append(f"PAGES_CRAWLED: {ctx.recon_pages_crawled}")

    # ── THE KEY FIX ──────────────────────────────────────────────────────────
    # Inject the literal string "LOGIN WALL DETECTED" so planner.py's
    # _detect_login_wall() function finds it by scanning spec text.
    # This works regardless of whether context dict is passed through.
    if ctx.login_wall_detected:
        lines.append("")
        lines.append("LOGIN WALL DETECTED")
        lines.append("=" * 60)
        lines.append("The crawler reached only the login/auth page.")
        lines.append("INSTRUCTION FOR PLANNER:")
        lines.append("  Step 0 = auth prerequisite (login as setup, not as test subject)")
        lines.append("  Steps 1-N = test ALL scenarios the user listed above")
        lines.append("  A plan with only login tests = WRONG when user asked for cart tests")
        lines.append("=" * 60)

    if ctx.recon_summary:
        lines.append("")
        lines.append("RECON SUMMARY (selectors/page structure only):")
        lines.append(ctx.recon_summary)

    lines.append("=" * 60)
    return "\n".join(lines)
