# agent/run_context.py
"""
Thread-safe per-request credential store.

Instead of os.environ (shared across all threads/workers),
each request gets its own RunContext stored in a contextvars.ContextVar.
This means two simultaneous users on AWS never see each other's credentials.

Usage:
    # In API handler (per request):
    ctx = RunContext.from_spec(spec_text)
    token = run_context.set(ctx)
    try:
        result = run_agent_from_spec(spec, ...)
    finally:
        run_context.reset(token)

    # In test code / planner:
    from agent.run_context import get_run_context
    ctx = get_run_context()
    phone = ctx.phone    # "8825594525" or ""
    otp   = ctx.otp      # "123456" or ""
"""
from __future__ import annotations
import re
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class RunContext:
    """Per-request credentials and config. Thread/async safe via ContextVar."""
    phone:   str = ""
    otp:     str = ""
    pincode: str = ""
    url:     str = ""

    # Derived — set after extraction
    spec_clean: str = ""   # spec with credentials stripped out

    @classmethod
    def from_spec(cls, spec: str) -> "RunContext":
        """Extract credentials from spec text and return a RunContext."""
        ctx = cls()

        # Mobile / phone
        m = re.search(r"(?:mobile|phone|number)\s*[:\-]\s*(\d{10,})", spec, re.I)
        if m:
            ctx.phone = m.group(1).strip()

        # OTP
        m = re.search(r"otp\s*[:\-]\s*(\d{4,8})", spec, re.I)
        if m:
            ctx.otp = m.group(1).strip()

        # Pincode
        m = re.search(r"pincode\s*[:\-]\s*(\d{4,8})", spec, re.I)
        if m:
            ctx.pincode = m.group(1).strip()

        # URL — first https:// found
        m = re.search(r"https?://[^\s\)\"\']+", spec)
        if m:
            ctx.url = m.group(0).rstrip(".,;")

        # Strip credentials from spec before sending to LLM
        clean = spec
        patterns = [
            r"(?:mobile|phone|number)\s*[:\-]\s*\d{10,}",
            r"otp\s*[:\-]\s*\d{4,8}",
            r"pincode\s*[:\-]\s*\d{4,8}",
            r"(?:use this login|login credentials if required)[^\n]*",
        ]
        for p in patterns:
            clean = re.sub(p, "", clean, flags=re.I)
        clean = re.sub(r"\n{3,}", "\n\n", clean).strip()

        if ctx.phone or ctx.otp or ctx.pincode:
            clean += "\n\n[AUTH NOTE: Credentials extracted. Agent will handle login as Step 0 prerequisite.]"

        ctx.spec_clean = clean
        return ctx

    @classmethod
    def from_request_body(cls, body: dict, spec: str) -> "RunContext":
        """
        Build RunContext from:
          1. Explicit creds in request body (from frontend JS extraction)
          2. Fallback: parse from spec text directly
        """
        creds = body.get("creds") or {}

        # Frontend may have already extracted them
        phone   = str(creds.get("mobile",  "") or "").strip()
        otp     = str(creds.get("otp",     "") or "").strip()
        pincode = str(creds.get("pincode", "") or "").strip()
        url     = str(creds.get("url",     "") or "").strip()

        # Also parse spec text as fallback
        spec_ctx = cls.from_spec(spec)

        return cls(
            phone   = phone   or spec_ctx.phone,
            otp     = otp     or spec_ctx.otp,
            pincode = pincode or spec_ctx.pincode,
            url     = url     or spec_ctx.url,
            spec_clean = spec_ctx.spec_clean,
        )

    def has_creds(self) -> bool:
        return bool(self.phone or self.otp)

    def apply_to_env(self):
        """
        Set credentials as process env vars.
        Only call this in single-threaded / single-worker setups.
        For multi-worker AWS use get_run_context() instead.
        """
        import os
        if self.phone:
            os.environ["JIOMART_PHONE"] = self.phone
            os.environ["TEST_MOBILE"]   = self.phone
        if self.otp:
            os.environ["JIOMART_OTP"]   = self.otp
            os.environ["TEST_OTP"]      = self.otp
        if self.pincode:
            os.environ["JIOMART_PINCODE"] = self.pincode
        if self.url:
            os.environ["JIOMART_URL"]   = self.url
            os.environ["APP_BASE_URL"]  = self.url
            os.environ["BASE_URL"]      = self.url


# ── ContextVar — one value per async task / thread ───────────
_run_context_var: ContextVar[Optional[RunContext]] = ContextVar(
    "run_context", default=None
)


def set_run_context(ctx: RunContext):
    """Set context for current request. Returns token for reset."""
    return _run_context_var.set(ctx)


def reset_run_context(token):
    """Reset after request completes."""
    _run_context_var.reset(token)


def get_run_context() -> RunContext:
    """
    Get credentials for the current request.
    Returns empty RunContext if not set (safe default).
    """
    ctx = _run_context_var.get()
    return ctx if ctx is not None else RunContext()


def get_phone()   -> str: return get_run_context().phone
def get_otp()     -> str: return get_run_context().otp
def get_pincode() -> str: return get_run_context().pincode
def get_url()     -> str: return get_run_context().url
