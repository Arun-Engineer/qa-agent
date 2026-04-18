"""agent/auth/plugins/form_login.py — Classic username+password form login.

Detection rules (in order):
  * The page has a form with a password field AND at least one text-like
    field (email, tel, username).
  * URL or title contains "login"/"sign in"/"signin".

Apply:
  * If a Playwright `browser_context` is provided, do a real browser login:
    locate the fields, type, submit, wait for network-idle, capture storage
    state (cookies + localStorage) so subsequent page navigations reuse auth.
  * If only a `http_session` (requests.Session) is provided, POST form data
    to the form's action URL and capture Set-Cookie headers.

The plugin is *defensive*: it never raises on missing fields — it returns
`AuthResult(ok=False, ...)` with a readable `message` so the executor can
record the failure as a finding.
"""
from __future__ import annotations

import re
from typing import Any

from agent.auth.base import AuthPlugin, AuthResult
from agent.auth.registry import register


_USERNAME_HINTS = ["email", "username", "user", "login", "phone", "mobile", "userid", "user_id"]
_PASSWORD_HINTS = ["password", "pass", "passwd", "pwd"]


def _match_field(forms: list[dict], hints: list[str], type_hint: str = "") -> tuple[str, str]:
    """Return (form_selector, field_selector) for the best matching field."""
    for form in forms:
        for field in form.get("fields", []):
            name = (field.get("name") or "").lower()
            f_type = (field.get("type") or "").lower()
            if type_hint and f_type == type_hint:
                return form.get("selector", "form"), f'[name="{field.get("name")}"]'
            if any(h in name for h in hints):
                return form.get("selector", "form"), f'[name="{field.get("name")}"]'
    return "", ""


class FormLoginPlugin:
    name = "form_login"

    def detect(self, ctx: dict[str, Any]) -> float:
        forms = ctx.get("detected_forms") or []
        url = (ctx.get("page_url") or "").lower()
        title = (ctx.get("page_title") or "").lower()

        has_pw = any(
            any((f.get("type") or "").lower() == "password"
                for f in form.get("fields", []))
            for form in forms
        )
        hint = any(k in f"{url} {title}" for k in
                   ("login", "signin", "sign in", "sign-in", "authenticate"))

        if has_pw and hint:
            return 0.95
        if has_pw:
            return 0.8
        if hint:
            return 0.4
        return 0.0

    def apply(self, ctx: dict[str, Any]) -> AuthResult:
        cred = ctx.get("credential")
        if not cred or not cred.username or not cred.password:
            return AuthResult(ok=False, plugin=self.name,
                              message="no credentials supplied")

        forms = ctx.get("detected_forms") or []
        form_sel, user_sel = _match_field(forms, _USERNAME_HINTS)
        _, pw_sel = _match_field(forms, _PASSWORD_HINTS, type_hint="password")
        if not user_sel or not pw_sel:
            return AuthResult(ok=False, plugin=self.name,
                              message="could not locate username/password fields")

        # ── Playwright path ─────────────────────────────────────────
        browser_ctx = ctx.get("browser_context")
        page_url = ctx.get("page_url") or ""
        if browser_ctx is not None:
            try:
                page = browser_ctx.new_page()
                page.goto(page_url, wait_until="domcontentloaded", timeout=20000)
                page.fill(f"{form_sel} {user_sel}".strip(), cred.username)
                page.fill(f"{form_sel} {pw_sel}".strip(), cred.password)
                # Best-effort submit: look for a submit button or press Enter.
                submitted = False
                for sel in ("button[type=submit]", "input[type=submit]",
                            "button:has-text('Sign in')", "button:has-text('Log in')"):
                    try:
                        if page.locator(f"{form_sel} {sel}").count() > 0:
                            page.click(f"{form_sel} {sel}")
                            submitted = True
                            break
                    except Exception:
                        continue
                if not submitted:
                    try:
                        page.press(f"{form_sel} {pw_sel}", "Enter")
                        submitted = True
                    except Exception:
                        pass

                page.wait_for_load_state("networkidle", timeout=10000)
                storage = browser_ctx.storage_state()
                final_url = page.url
                page.close()

                # Heuristic success: after login, URL should not still look
                # like a login page.
                still_login = any(k in final_url.lower() for k in ("login", "signin"))
                ok = submitted and not still_login
                return AuthResult(
                    ok=ok, plugin=self.name,
                    message=f"submitted={submitted} final_url={final_url}",
                    storage_state=storage,
                    meta={"final_url": final_url},
                )
            except Exception as e:
                return AuthResult(ok=False, plugin=self.name,
                                  message=f"browser login error: {e}")

        # ── HTTP path ───────────────────────────────────────────────
        http = ctx.get("http_session")
        action = ""
        if forms:
            action = forms[0].get("action") or page_url
        if http is not None and action:
            try:
                payload = {
                    user_sel.strip('[]"=name '): cred.username,
                    pw_sel.strip('[]"=name '): cred.password,
                }
                r = http.post(action, data=payload, timeout=20, allow_redirects=True)
                cookies = {c.name: c.value for c in http.cookies}
                ok = r.ok and not any(k in r.url.lower() for k in ("login", "signin"))
                return AuthResult(ok=ok, plugin=self.name,
                                  message=f"status={r.status_code} final={r.url}",
                                  cookies=cookies,
                                  meta={"final_url": r.url,
                                        "status_code": r.status_code})
            except Exception as e:
                return AuthResult(ok=False, plugin=self.name,
                                  message=f"http login error: {e}")

        return AuthResult(ok=False, plugin=self.name,
                          message="no browser_context or http_session provided")


register(FormLoginPlugin())
