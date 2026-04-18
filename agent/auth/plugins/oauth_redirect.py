"""agent/auth/plugins/oauth_redirect.py — OAuth 2.0 Authorization-Code flow.

Detection: page redirects to a recognized identity provider (Google, Microsoft,
Auth0, Okta, Keycloak) or the URL contains `/oauth/authorize`.

Apply: Only usable via a Playwright browser context (cannot replay OAuth
purely with requests). The plugin drives the provider login page with the
shared credential and waits for the callback URL to return to the app origin.

This is a *scaffold* that handles the most common identity-provider patterns.
More exotic IdP flows (device code, SAML, SSO w/ MFA) will need dedicated
sub-plugins — that's why detection scores max out at 0.75 here so a more
specific future plugin can override.
"""
from __future__ import annotations

import re
from typing import Any

from agent.auth.base import AuthPlugin, AuthResult
from agent.auth.registry import register


_IDP_SIGNATURES = [
    r"accounts\.google\.com",
    r"login\.microsoftonline\.com",
    r"login\.live\.com",
    r"auth\d?\.",
    r"okta\.com",
    r"auth0\.com",
    r"keycloak",
]


def _is_idp_url(url: str) -> bool:
    u = (url or "").lower()
    return any(re.search(pat, u) for pat in _IDP_SIGNATURES) or "oauth/authorize" in u


class OAuthRedirectPlugin:
    name = "oauth_redirect"

    def detect(self, ctx: dict[str, Any]) -> float:
        url = (ctx.get("page_url") or "").lower()
        redirected = ctx.get("redirected_from") or ""
        if _is_idp_url(url):
            return 0.75
        if _is_idp_url(redirected):
            return 0.65
        return 0.0

    def apply(self, ctx: dict[str, Any]) -> AuthResult:
        cred = ctx.get("credential")
        browser_ctx = ctx.get("browser_context")
        if not cred or not browser_ctx:
            return AuthResult(ok=False, plugin=self.name,
                              message="OAuth requires browser_context + credentials")
        app_origin = ctx.get("app_origin") or ""
        login_url = ctx.get("page_url") or ""
        try:
            page = browser_ctx.new_page()
            page.goto(login_url, wait_until="domcontentloaded", timeout=25000)

            # Best-effort: IdPs commonly have a sequence of email → next → password.
            # Try email-first pattern.
            if page.locator('input[type="email"]').count() > 0:
                page.fill('input[type="email"]', cred.username)
                for nxt in ("button:has-text('Next')", "button:has-text('Continue')",
                            "input[type=submit]", "#identifierNext"):
                    try:
                        if page.locator(nxt).count() > 0:
                            page.click(nxt)
                            page.wait_for_load_state("networkidle", timeout=8000)
                            break
                    except Exception:
                        continue
            # Password step.
            if page.locator('input[type="password"]').count() > 0:
                page.fill('input[type="password"]', cred.password)
                for sub in ("button[type=submit]", "button:has-text('Sign in')",
                            "button:has-text('Log in')", "#passwordNext"):
                    try:
                        if page.locator(sub).count() > 0:
                            page.click(sub)
                            break
                    except Exception:
                        continue

            # Wait until we're redirected back to the app origin OR a clear
            # MFA/consent page appears.
            try:
                page.wait_for_url(re.compile(re.escape(app_origin) + r".*"),
                                  timeout=20000)
            except Exception:
                pass

            storage = browser_ctx.storage_state()
            final_url = page.url
            page.close()
            back_at_app = app_origin and final_url.startswith(app_origin)
            return AuthResult(ok=bool(back_at_app), plugin=self.name,
                              message=f"final_url={final_url}",
                              storage_state=storage,
                              meta={"final_url": final_url})
        except Exception as e:
            return AuthResult(ok=False, plugin=self.name,
                              message=f"oauth flow error: {e}")


register(OAuthRedirectPlugin())
