"""agent/auth/plugins/bearer_token.py — Bearer-token / API-key auth.

Detection:
  * XHR responses seen during crawl with 401 + `Authorization: Bearer` hint
    in WWW-Authenticate header.
  * Credential `extras` contains a pre-issued token (user pasted it into the
    TOTP/extras field).

Apply: attach `Authorization: Bearer <token>` to every subsequent request.
"""
from __future__ import annotations

from typing import Any

from agent.auth.base import AuthPlugin, AuthResult
from agent.auth.registry import register


class BearerTokenPlugin:
    name = "bearer_token"

    def detect(self, ctx: dict[str, Any]) -> float:
        cred = ctx.get("credential")
        extras = (cred.extras if cred else {}) or {}
        if extras.get("bearer_token") or extras.get("api_key"):
            return 0.9
        headers = {k.lower(): v for k, v in (ctx.get("response_headers") or {}).items()}
        www_auth = headers.get("www-authenticate", "").lower()
        if "bearer" in www_auth:
            return 0.8
        return 0.0

    def apply(self, ctx: dict[str, Any]) -> AuthResult:
        cred = ctx.get("credential")
        if not cred:
            return AuthResult(ok=False, plugin=self.name, message="no credential")
        token = (cred.extras or {}).get("bearer_token") or \
                (cred.extras or {}).get("api_key") or \
                cred.password
        if not token:
            return AuthResult(ok=False, plugin=self.name,
                              message="no token in credential.extras['bearer_token'|'api_key'] or password")
        header = {"Authorization": f"Bearer {token}"}
        http = ctx.get("http_session")
        if http is not None:
            http.headers.update(header)
        return AuthResult(ok=True, plugin=self.name,
                          message="bearer token attached",
                          headers=header)


register(BearerTokenPlugin())
