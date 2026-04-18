"""agent/auth/plugins/otp_sms.py — Mobile OTP login (popular in India / APAC).

Detection:
  * Login form has a phone/mobile field (tel input or name matches phone hints)
    AND no password field, OR
  * URL / title mentions "OTP" / "verify"

Apply:
  * Submit the phone number.
  * Wait for an OTP input to appear.
  * Pull the OTP from either:
      - `credential.extras["otp"]` (user-typed, static)
      - `credential.totp_seed` (TOTP, code generated on the fly via pyotp)
      - An SMS reader callback configured via AUTO_OTP_CALLBACK_URL (optional)
  * Fill + submit.

MFA during password login is handled by form_login plugin handing control
here when it sees an OTP field appear post-submit — that coordination is
done by the executor, not here.
"""
from __future__ import annotations

import os
import re
import time
from typing import Any

from agent.auth.base import AuthPlugin, AuthResult
from agent.auth.registry import register


_OTP_FIELD_SELECTORS = [
    'input[name*="otp" i]', 'input[id*="otp" i]',
    'input[name*="code" i]', 'input[id*="code" i]',
    'input[autocomplete="one-time-code"]',
    'input[name*="verify" i]', 'input[name*="token" i]',
]
_PHONE_FIELD_HINTS = ["phone", "mobile", "tel", "msisdn", "number"]


def _derive_otp(cred) -> str:
    """Pull an OTP value from the credential in priority order."""
    extras = cred.extras or {}
    # 1) static OTP the user typed in (e.g. a dev environment with a known code)
    if extras.get("otp"):
        return str(extras["otp"])
    # 2) TOTP seed → generate current code
    seed = cred.totp_seed or extras.get("totp_seed") or ""
    if seed:
        try:
            import pyotp  # type: ignore
            return pyotp.TOTP(seed).now()
        except Exception:
            pass
    # 3) External callback (e.g. Twilio webhook) — not implemented here,
    #    but env hook is declared so the integration point is visible.
    cb = os.getenv("AUTO_OTP_CALLBACK_URL", "").strip()
    if cb:
        try:
            import requests
            r = requests.get(cb, timeout=10, params={"phone": cred.username})
            if r.ok:
                m = re.search(r"\b\d{4,8}\b", r.text)
                if m:
                    return m.group(0)
        except Exception:
            pass
    return ""


class OtpSmsPlugin:
    name = "otp_sms"

    def detect(self, ctx: dict[str, Any]) -> float:
        forms = ctx.get("detected_forms") or []
        url = (ctx.get("page_url") or "").lower()
        title = (ctx.get("page_title") or "").lower()
        has_phone = False
        has_password = False
        for f in forms:
            for field in f.get("fields", []):
                name = (field.get("name") or "").lower()
                f_type = (field.get("type") or "").lower()
                if f_type == "tel" or any(h in name for h in _PHONE_FIELD_HINTS):
                    has_phone = True
                if f_type == "password":
                    has_password = True
        hinted = any(k in f"{url} {title}" for k in ("otp", "verify", "verification"))
        if has_phone and not has_password:
            return 0.9
        if hinted:
            return 0.6
        return 0.0

    def apply(self, ctx: dict[str, Any]) -> AuthResult:
        cred = ctx.get("credential")
        browser_ctx = ctx.get("browser_context")
        page_url = ctx.get("page_url") or ""
        if not cred or not cred.username:
            return AuthResult(ok=False, plugin=self.name,
                              message="phone number required in credential.username")
        if browser_ctx is None:
            return AuthResult(ok=False, plugin=self.name,
                              message="OTP login requires browser_context")
        try:
            page = browser_ctx.new_page()
            page.goto(page_url, wait_until="domcontentloaded", timeout=20000)

            # Fill phone + submit step 1.
            phone_sel = None
            for s in ('input[type="tel"]', 'input[name*="phone" i]',
                      'input[name*="mobile" i]', 'input[name*="msisdn" i]'):
                if page.locator(s).count() > 0:
                    phone_sel = s
                    break
            if not phone_sel:
                return AuthResult(ok=False, plugin=self.name,
                                  message="no phone input field found")
            page.fill(phone_sel, cred.username)
            for sub in ("button[type=submit]", "button:has-text('Send')",
                        "button:has-text('Continue')", "button:has-text('Next')",
                        "button:has-text('Get OTP')"):
                if page.locator(sub).count() > 0:
                    page.click(sub)
                    break
            # Wait for OTP field to appear.
            page.wait_for_load_state("networkidle", timeout=8000)
            otp_sel = None
            t0 = time.time()
            while time.time() - t0 < 15:
                for s in _OTP_FIELD_SELECTORS:
                    if page.locator(s).count() > 0:
                        otp_sel = s
                        break
                if otp_sel:
                    break
                time.sleep(0.5)
            if not otp_sel:
                return AuthResult(ok=False, plugin=self.name,
                                  message="OTP input never appeared")

            otp = _derive_otp(cred)
            if not otp:
                return AuthResult(ok=False, plugin=self.name,
                                  message="could not derive OTP (set credential.extras['otp'] or totp_seed)")
            page.fill(otp_sel, otp)
            for sub in ("button[type=submit]", "button:has-text('Verify')",
                        "button:has-text('Submit')", "button:has-text('Confirm')"):
                if page.locator(sub).count() > 0:
                    page.click(sub)
                    break
            page.wait_for_load_state("networkidle", timeout=10000)
            final_url = page.url
            storage = browser_ctx.storage_state()
            page.close()
            still_login = any(k in final_url.lower() for k in ("login", "otp", "verify"))
            return AuthResult(ok=not still_login, plugin=self.name,
                              message=f"final_url={final_url}",
                              storage_state=storage,
                              meta={"final_url": final_url})
        except Exception as e:
            return AuthResult(ok=False, plugin=self.name,
                              message=f"otp flow error: {e}")


register(OtpSmsPlugin())
