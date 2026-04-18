"""agent/auth/plugins/basic_auth.py — HTTP Basic authentication.

Detection: server sent `WWW-Authenticate: Basic …` OR status was 401 with no
login-form present.

Apply: set an `Authorization: Basic <base64>` header.
"""
from __future__ import annotations

import base64
from typing import Any

from agent.auth.base import AuthPlugin, AuthResult
from agent.auth.registry import register


class BasicAuthPlugin:
    name = "basic_auth"

    def detect(self, ctx: dict[str, Any]) -> float:
        headers = {k.lower(): v for k, v in (ctx.get("response_headers") or {}).items()}
        www_auth = headers.get("www-authenticate", "").lower()
        status = int(ctx.get("status_code") or 0)
        has_forms = bool(ctx.get("detected_forms"))
        if www_auth.startswith("basic"):
            return 0.98
        if status == 401 and not has_forms:
            return 0.7
        return 0.0

    def apply(self, ctx: dict[str, Any]) -> AuthResult:
        cred = ctx.get("credential")
        if not cred or not cred.username:
            return AuthResult(ok=False, plugin=self.name,
                              message="credentials required")
        raw = f"{cred.username}:{cred.password}".encode("utf-8")
        token = base64.b64encode(raw).decode("ascii")
        header = {"Authorization": f"Basic {token}"}

        # If a session is available, attach the header and probe.
        http = ctx.get("http_session")
        if http is not None:
            http.headers.update(header)
            try:
                r = http.get(ctx.get("page_url") or "/", timeout=15)
                ok = r.status_code not in (401, 403)
                return AuthResult(ok=ok, plugin=self.name,
                                  message=f"status={r.status_code}",
                                  headers=header,
                                  meta={"status_code": r.status_code})
            except Exception as e:
                return AuthResult(ok=False, plugin=self.name, message=str(e))
        return AuthResult(ok=True, plugin=self.name,
                          message="header prepared (no session to test)",
                          headers=header)


register(BasicAuthPlugin())
