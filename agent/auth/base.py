"""agent/auth/base.py — Contract every auth plugin implements.

An auth plugin answers two questions:

  1. Can I handle this URL / HTTP response?  → `detect(ctx) -> float`
     Higher score wins. 0 means "no".
  2. Make the agent authenticated as this role. → `apply(ctx) -> AuthResult`

The "ctx" is a lightweight dict containing everything the plugin might need:
  - page_url, detected_forms, response_headers, status_code
  - credential (from cred_vault)
  - browser session (Playwright BrowserContext) OR requests.Session
  - execution_profile

Plugins are stateless — they return an AuthResult describing what happened
and what the executor should do next (set cookies, set headers, re-issue
request, etc.).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class AuthResult:
    """Outcome of an auth attempt."""
    ok: bool
    plugin: str
    message: str = ""
    # Cookies to inject into subsequent requests (name -> value).
    cookies: dict[str, str] = field(default_factory=dict)
    # HTTP headers to inject (e.g. Authorization: Bearer …).
    headers: dict[str, str] = field(default_factory=dict)
    # If a Playwright storage_state blob is produced, include it here so the
    # executor can reuse the authenticated browser context.
    storage_state: dict[str, Any] = field(default_factory=dict)
    # Anything else the plugin wants to surface (debug, diagnostics).
    meta: dict[str, Any] = field(default_factory=dict)


class AuthPlugin(Protocol):
    """Minimal protocol every plugin satisfies."""

    name: str

    def detect(self, ctx: dict[str, Any]) -> float:
        """Return a confidence score in [0.0, 1.0]. 0 = cannot handle."""
        ...

    def apply(self, ctx: dict[str, Any]) -> AuthResult:
        """Authenticate. Return an AuthResult."""
        ...
