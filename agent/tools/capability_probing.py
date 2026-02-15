# agent/tools/capability_probing.py
from __future__ import annotations

import re
import sys
import socket
import subprocess
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

try:
    import requests  # preferred
except Exception:  # pragma: no cover
    requests = None


CAPTCHA_PATTERNS = [
    r"captcha",
    r"verify you are human",
    r"cloudflare",
    r"cf-ray",
    r"turnstile",
    r"hcaptcha",
    r"recaptcha",
    r"access denied",
    r"bot detection",
]


def _normalize_base_url(base_url: str) -> str:
    base_url = (base_url or "").strip()
    if not base_url:
        return ""
    if not re.match(r"^https?://", base_url, flags=re.I):
        base_url = "https://" + base_url
    # remove trailing slash
    return base_url.rstrip("/")


def _host_port(base_url: str) -> tuple[str, int]:
    u = urlparse(base_url)
    host = u.hostname or ""
    port = u.port or (443 if u.scheme == "https" else 80)
    return host, port


def _tcp_ping(host: str, port: int, timeout: float = 3.0) -> bool:
    if not host:
        return False
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


def _http_probe(base_url: str, timeout: int = 10) -> Dict[str, Any]:
    """
    Lightweight HTTP probe:
    - checks if URL is reachable
    - catches common block/captcha signals
    - returns status code and final URL
    """
    out: Dict[str, Any] = {
        "ok": False,
        "status_code": None,
        "final_url": None,
        "blocked": False,
        "captcha_like": False,
        "reason": None,
    }

    if not base_url:
        out["reason"] = "base_url_empty"
        return out

    if requests is None:
        out["reason"] = "requests_not_installed"
        return out

    try:
        resp = requests.get(
            base_url,
            timeout=timeout,
            allow_redirects=True,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                )
            },
        )
        out["status_code"] = resp.status_code
        out["final_url"] = str(resp.url)

        text = (resp.text or "")[:50000].lower()
        header_blob = " ".join([f"{k}:{v}" for k, v in resp.headers.items()]).lower()

        captcha_hit = any(re.search(p, text, flags=re.I) for p in CAPTCHA_PATTERNS) or any(
            re.search(p, header_blob, flags=re.I) for p in CAPTCHA_PATTERNS
        )
        out["captcha_like"] = bool(captcha_hit)

        if resp.status_code in (401, 403):
            out["blocked"] = True
            out["reason"] = f"http_{resp.status_code}_blocked"
            out["ok"] = False
            return out

        if resp.status_code == 429:
            out["blocked"] = True
            out["reason"] = "http_429_rate_limited"
            out["ok"] = False
            return out

        if 500 <= resp.status_code <= 599:
            out["reason"] = f"http_{resp.status_code}_server_error"
            out["ok"] = False
            return out

        # 2xx/3xx generally ok
        out["ok"] = True
        return out

    except requests.exceptions.SSLError:
        out["reason"] = "ssl_error"
        return out
    except requests.exceptions.ConnectionError:
        out["reason"] = "connection_error"
        return out
    except requests.exceptions.Timeout:
        out["reason"] = "timeout"
        return out
    except Exception as e:
        out["reason"] = f"unknown_http_error: {e}"
        return out


def _probe_playwright_runtime(timeout: int = 20) -> Dict[str, Any]:
    """
    Validates:
    - playwright import works
    - chromium can launch headless (means browsers are installed)
    """
    out: Dict[str, Any] = {
        "playwright_importable": False,
        "chromium_launchable": False,
        "reason": None,
    }

    # 1) Import check (in current process)
    try:
        import playwright  # noqa: F401
        out["playwright_importable"] = True
    except Exception as e:
        out["reason"] = f"playwright_import_failed: {e}"
        return out

    # 2) Launch check (in subprocess; avoids hanging your server)
    code = r"""
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    page = b.new_page()
    page.goto("about:blank")
    b.close()
print("OK")
"""
    try:
        proc = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if proc.returncode == 0 and "OK" in (proc.stdout or ""):
            out["chromium_launchable"] = True
            return out

        # common failure: browsers not installed
        err = (proc.stderr or proc.stdout or "").strip()
        out["reason"] = f"chromium_launch_failed: {err[-600:]}"
        return out

    except subprocess.TimeoutExpired:
        out["reason"] = "chromium_launch_timeout"
        return out
    except Exception as e:
        out["reason"] = f"chromium_launch_error: {e}"
        return out


def probe_capabilities(base_url: str, *, http_timeout: int = 10) -> Dict[str, Any]:
    """
    Returns a decision for recon:
      - ok: should we proceed at all
      - mode: full/degraded/skip
      - max_pages/max_depth suggestion
      - reasons: list of reasons found

    IMPORTANT: This does NOT “hack” or do aggressive scanning.
    It's just: TCP reachability + one GET + a quick playwright launch check.
    """
    base_url = _normalize_base_url(base_url)
    reasons: List[str] = []

    if not base_url:
        return {
            "ok": False,
            "mode": "skip",
            "base_url": base_url,
            "reasons": ["base_url_missing"],
            "max_pages": 0,
            "max_depth": 0,
        }

    host, port = _host_port(base_url)
    tcp_ok = _tcp_ping(host, port, timeout=3.0)
    if not tcp_ok:
        return {
            "ok": False,
            "mode": "skip",
            "base_url": base_url,
            "reasons": [f"tcp_unreachable:{host}:{port}"],
            "max_pages": 0,
            "max_depth": 0,
        }

    http = _http_probe(base_url, timeout=http_timeout)
    if not http.get("ok"):
        # blocked / captcha / rate limited -> skip recon
        reasons.append(str(http.get("reason") or "http_probe_failed"))
        if http.get("captcha_like"):
            reasons.append("captcha_like_detected")
        return {
            "ok": False,
            "mode": "skip",
            "base_url": base_url,
            "final_url": http.get("final_url"),
            "http_status": http.get("status_code"),
            "reasons": reasons,
            "max_pages": 0,
            "max_depth": 0,
        }

    if http.get("captcha_like"):
        # recon likely useless if automation blocked
        reasons.append("captcha_like_detected")

    pw = _probe_playwright_runtime(timeout=20)
    if not pw.get("playwright_importable"):
        reasons.append(str(pw.get("reason") or "playwright_missing"))
        # can still do degraded recon (requests-only crawl) if your ui_recon supports it
        return {
            "ok": True,
            "mode": "degraded",
            "base_url": base_url,
            "final_url": http.get("final_url"),
            "http_status": http.get("status_code"),
            "reasons": reasons,
            "max_pages": 5,
            "max_depth": 1,
        }

    if not pw.get("chromium_launchable"):
        reasons.append(str(pw.get("reason") or "playwright_browsers_missing"))
        return {
            "ok": True,
            "mode": "degraded",
            "base_url": base_url,
            "final_url": http.get("final_url"),
            "http_status": http.get("status_code"),
            "reasons": reasons,
            "max_pages": 5,
            "max_depth": 1,
        }

    # everything looks OK
    return {
        "ok": True,
        "mode": "full",
        "base_url": base_url,
        "final_url": http.get("final_url"),
        "http_status": http.get("status_code"),
        "reasons": reasons,
        "max_pages": 25,
        "max_depth": 2,
    }
