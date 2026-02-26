import os
import re
import pytest
from playwright.sync_api import Page, expect

APP_BASE_URL = os.getenv("APP_BASE_URL") or os.getenv("BASE_URL") or "https://opensource-demo.orangehrmlive.com"
LOGIN_URL = APP_BASE_URL.rstrip("/") + "/web/index.php/auth/login"

CASES = [
    {"name": "invalid_username_invalid_password", "username": "invalidUser", "password": "invalidPass", "expect": "invalid_credentials"},
    {"name": "empty_username_empty_password", "username": "", "password": "", "expect": "required"},
    {"name": "whitespace_username_whitespace_password", "username": "   ", "password": "   ", "expect": "required"},
    {"name": "special_chars_username_special_chars_password", "username": "!@#$%^&*()", "password": "!@#$%^&*()", "expect": "invalid_credentials"},
    {"name": "unicode_username_unicode_password", "username": "用户", "password": "密码", "expect": "invalid_credentials"},
    {"name": "repeated_attempts", "username": "invalidUser", "password": "invalidPass", "expect": "invalid_credentials", "repeat": 3},
]

def assert_invalid_credentials(page: Page):
    # OrangeHRM invalid login banner
    alert = page.locator("div.oxd-alert.oxd-alert--error")
    expect(alert).to_be_visible(timeout=10000)

    msg = alert.locator(".oxd-alert-content-text")
    # if text changes slightly, this still passes as long as banner exists
    expect(msg).to_be_visible()

    # stay on login page
    expect(page).to_have_url(re.compile(r".*/auth/login"))

def assert_required(page: Page):
    # Field validation messages
    required_msgs = page.locator("span.oxd-input-field-error-message")
    expect(required_msgs.first).to_be_visible(timeout=5000)

    texts = required_msgs.all_text_contents()
    assert any("Required" in t for t in texts), f"Expected 'Required' validation, got: {texts}"

@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_login_invalid_credentials(page: Page, case):
    repeat = case.get("repeat", 1)

    for _ in range(repeat):
        page.goto(LOGIN_URL)
        page.fill('input[name="username"]', case["username"])
        page.fill('input[name="password"]', case["password"])
        page.click('button[type="submit"]')

        if case["expect"] == "required":
            assert_required(page)
        else:
            assert_invalid_credentials(page)
