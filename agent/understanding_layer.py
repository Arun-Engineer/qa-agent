# agent/understanding_layer.py
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Optional, Tuple, Dict, Any
from urllib.parse import urlparse

URL_RE = re.compile(r"https?://[^\s\)\"\']+")


def _first_url(text: str) -> Optional[str]:
    m = URL_RE.search(text or "")
    if not m:
        return None
    return m.group(0).rstrip(").,;\"'")


def _normalize_base_url(url: str) -> Optional[str]:
    if not url:
        return None
    p = urlparse(url)
    if not p.scheme or not p.netloc:
        return None
    return f"{p.scheme}://{p.netloc}".rstrip("/")


def _load_recon_runner():
    # supports either agent/tools/ui_recon_runner.py or agent/ui_recon_runner.py
    try:
        from agent.tools.ui_recon_runner import run_recon  # type: ignore
        return run_recon
    except Exception:
        try:
            from agent.ui_recon_runner import run_recon  # type: ignore
            return run_recon
        except Exception:
            return None


@dataclass
class UnderstandingContext:
    base_url: Optional[str] = None
    recon_status: Optional[str] = None
    site_model_path: Optional[str] = None
    recon_summary: str = ""
    recon_pages_crawled: Optional[int] = None


def enrich_spec_with_understanding(
    spec: str,
    max_pages: int = 25,
    max_depth: int = 2,
) -> Tuple[str, UnderstandingContext]:
    """
    Returns:
      enriched_spec: original spec + recon summary + site model path (if available)
      ctx: UnderstandingContext
    """
    ctx = UnderstandingContext()
    spec = (spec or "").strip()

    # 1) base url
    url_in_spec = _first_url(spec)
    base_url = _normalize_base_url(url_in_spec or "")
    if not base_url:
        # allow user to set it externally
        base_url = os.getenv("APP_BASE_URL") or os.getenv("BASE_URL")
        base_url = _normalize_base_url(base_url or "")

    ctx.base_url = base_url

    # 2) optional recon (can be disabled)
    if not base_url:
        return spec, ctx

    if os.getenv("QA_DISABLE_RECON", "").strip() in ("1", "true", "TRUE", "yes", "YES"):
        os.environ["APP_BASE_URL"] = base_url
        os.environ["BASE_URL"] = base_url
        return spec, ctx

    run_recon = _load_recon_runner()
    if not run_recon:
        # recon runner not installed/available
        os.environ["APP_BASE_URL"] = base_url
        os.environ["BASE_URL"] = base_url
        return spec, ctx

    try:
        recon = run_recon(base_url=base_url, max_pages=max_pages, max_depth=max_depth)
    except Exception as e:
        ctx.recon_status = "error"
        ctx.recon_summary = f"Recon failed: {e}"
        os.environ["APP_BASE_URL"] = base_url
        os.environ["BASE_URL"] = base_url
        return spec, ctx

    ctx.recon_status = recon.get("status")
    ctx.site_model_path = recon.get("model_path")
    ctx.recon_summary = (recon.get("summary") or "").strip()
    ctx.recon_pages_crawled = recon.get("pages_crawled")

    # export envs for tests/generator
    os.environ["APP_BASE_URL"] = base_url
    os.environ["BASE_URL"] = base_url
    if ctx.site_model_path:
        os.environ["SITE_MODEL_PATH"] = ctx.site_model_path

    # 3) enriched spec block (this is what boosts “intelligence”)
    block = [
        "\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "SITE CONTEXT (AUTO-DISCOVERED)",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"BASE_URL: {base_url}",
        f"RECON_STATUS: {ctx.recon_status}",
        f"PAGES_CRAWLED: {ctx.recon_pages_crawled}",
        f"SITE_MODEL_PATH: {ctx.site_model_path}",
        "",
        "RECON_SUMMARY:",
        ctx.recon_summary or "(empty)",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n",
    ]

    return spec + "\n".join(block), ctx
