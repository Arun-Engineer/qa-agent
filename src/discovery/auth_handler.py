"""Auth Handler — Detect login type, autofill credentials, manage cookies.

Moved from auth/ per v5 architecture. Supports:
  - Form-based login (username/password fields)
  - OAuth redirect detection (not autofilled, just detected)
  - Cookie/token persistence across crawl pages
"""
from __future__ import annotations

import structlog
from typing import Optional
from dataclasses import dataclass

logger = structlog.get_logger()


@dataclass
class LoginResult:
    success: bool
    method: str             # form_login | oauth_detected | cookie_injected | skipped
    url_after_login: str = ""
    cookies_count: int = 0
    error: Optional[str] = None


# Common selectors for login fields across most web apps
LOGIN_FIELD_SELECTORS = {
    "username": [
        'input[name="email"]', 'input[name="username"]', 'input[name="login"]',
        'input[type="email"]', 'input[id="email"]', 'input[id="username"]',
        'input[autocomplete="username"]', 'input[autocomplete="email"]',
    ],
    "password": [
        'input[name="password"]', 'input[type="password"]',
        'input[id="password"]', 'input[autocomplete="current-password"]',
    ],
    "submit": [
        'button[type="submit"]', 'input[type="submit"]',
        'button:has-text("Log in")', 'button:has-text("Sign in")',
        'button:has-text("Login")', 'button:has-text("Submit")',
    ],
}

# Patterns that indicate an OAuth/SSO redirect
OAUTH_INDICATORS = [
    "accounts.google.com", "login.microsoftonline.com", "github.com/login/oauth",
    "auth0.com", "okta.com", "cognito", "/oauth2/authorize", "/saml/",
]


def detect_login_type(page) -> str:
    """Detect what kind of login page this is.

    Args:
        page: Playwright Page object

    Returns:
        'form' | 'oauth' | 'unknown'
    """
    try:
        url = page.url.lower()

        # Check for OAuth indicators in URL
        for pattern in OAUTH_INDICATORS:
            if pattern in url:
                logger.info("auth_detected_oauth", url=url, pattern=pattern)
                return "oauth"

        # Check for password field (strong indicator of form login)
        for selector in LOGIN_FIELD_SELECTORS["password"]:
            try:
                el = page.query_selector(selector)
                if el and el.is_visible():
                    logger.info("auth_detected_form", url=url)
                    return "form"
            except Exception:
                continue

        return "unknown"
    except Exception as e:
        logger.error("auth_detection_failed", error=str(e))
        return "unknown"


def perform_login(page, login_url: str, username: str, password: str) -> LoginResult:
    """Navigate to login page and fill in credentials.

    Args:
        page: Playwright Page object
        login_url: URL of the login page
        username: Username/email to fill
        password: Password to fill

    Returns:
        LoginResult with success status and details
    """
    try:
        page.goto(login_url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(2000)  # wait for SPA rendering

        login_type = detect_login_type(page)

        if login_type == "oauth":
            logger.info("auth_oauth_detected", url=login_url)
            return LoginResult(
                success=False, method="oauth_detected",
                url_after_login=page.url,
                error="OAuth login detected — manual intervention required",
            )

        if login_type == "unknown":
            logger.warning("auth_no_login_form", url=login_url)
            return LoginResult(
                success=False, method="skipped",
                url_after_login=page.url,
                error="No recognizable login form found",
            )

        # Form login: find and fill fields
        username_filled = False
        for selector in LOGIN_FIELD_SELECTORS["username"]:
            try:
                el = page.query_selector(selector)
                if el and el.is_visible():
                    el.click()
                    el.fill(username)
                    username_filled = True
                    logger.info("auth_filled_username", selector=selector)
                    break
            except Exception:
                continue

        if not username_filled:
            return LoginResult(
                success=False, method="form_login",
                url_after_login=page.url,
                error="Could not find username/email field",
            )

        password_filled = False
        for selector in LOGIN_FIELD_SELECTORS["password"]:
            try:
                el = page.query_selector(selector)
                if el and el.is_visible():
                    el.click()
                    el.fill(password)
                    password_filled = True
                    logger.info("auth_filled_password", selector=selector)
                    break
            except Exception:
                continue

        if not password_filled:
            return LoginResult(
                success=False, method="form_login",
                url_after_login=page.url,
                error="Could not find password field",
            )

        # Click submit
        submitted = False
        for selector in LOGIN_FIELD_SELECTORS["submit"]:
            try:
                el = page.query_selector(selector)
                if el and el.is_visible():
                    el.click()
                    submitted = True
                    logger.info("auth_submitted", selector=selector)
                    break
            except Exception:
                continue

        if not submitted:
            # Fallback: press Enter
            page.keyboard.press("Enter")
            logger.info("auth_submitted_enter_fallback")

        # Wait for navigation
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            page.wait_for_timeout(3000)

        # Check if login succeeded (heuristic: URL changed away from login page)
        current_url = page.url.lower()
        login_keywords = ["login", "signin", "sign-in", "auth"]
        still_on_login = any(kw in current_url for kw in login_keywords)

        # Also check for error messages
        error_selectors = [
            '.error', '.alert-danger', '.login-error', '[class*="error"]',
            'text="Invalid"', 'text="incorrect"',
        ]
        has_error = False
        for sel in error_selectors:
            try:
                el = page.query_selector(sel)
                if el and el.is_visible():
                    has_error = True
                    break
            except Exception:
                continue

        cookies = page.context.cookies()
        success = not still_on_login and not has_error

        if success:
            logger.info("auth_login_success", url_after=page.url, cookies=len(cookies))
        else:
            logger.warning("auth_login_failed", url=page.url, still_on_login=still_on_login, has_error=has_error)

        return LoginResult(
            success=success,
            method="form_login",
            url_after_login=page.url,
            cookies_count=len(cookies),
            error=None if success else "Login appears to have failed (still on login page or error detected)",
        )

    except Exception as e:
        logger.error("auth_login_exception", error=str(e))
        return LoginResult(
            success=False, method="form_login",
            url_after_login=getattr(page, "url", ""),
            error=str(e),
        )
