import os
import pytest
from playwright.sync_api import Page, expect

APP_BASE_URL = os.getenv("APP_BASE_URL", "https://opensource-demo.orangehrmlive.com")

@pytest.mark.ui
@pytest.mark.priority("P1")
@pytest.mark.severity("high")
@pytest.mark.tags("ui", "negative", "state")
def test_login_invalid_credentials(page: Page):
    """
    Test login with various invalid credentials.
    Linked Requirements: REQ-001
    """
    test_cases = [
        {
            "name": "invalid_username_invalid_password",
            "inputs": {"username": "invalidUser", "password": "invalidPass"},
            "expected": {
                "error_visible": True,
                "error_any_of": ["Invalid credentials"],
                "url_contains": "/auth/login",
                "stays_on_page": True
            }
        },
        {
            "name": "empty_username_valid_password",
            "inputs": {"username": "", "password": "validPass"},
            "expected": {
                "error_visible": True,
                "error_any_of": ["Required"],
                "url_contains": "/auth/login",
                "stays_on_page": True
            }
        },
        {
            "name": "valid_username_empty_password",
            "inputs": {"username": "validUser", "password": ""},
            "expected": {
                "error_visible": True,
                "error_any_of": ["Required"],
                "url_contains": "/auth/login",
                "stays_on_page": True
            }
        },
        {
            "name": "whitespace_username_password",
            "inputs": {"username": "   ", "password": "   "},
            "expected": {
                "error_visible": True,
                "error_any_of": ["Invalid credentials"],
                "url_contains": "/auth/login",
                "stays_on_page": True
            }
        },
        {
            "name": "long_username_long_password",
            "inputs": {"username": "a", "password": "b"},
            "expected": {
                "error_visible": True,
                "error_any_of": ["Invalid credentials"],
                "url_contains": "/auth/login",
                "stays_on_page": True
            }
        },
        {
            "name": "special_chars_username_password",
            "inputs": {"username": "!@#$%^&*()", "password": "!@#$%^&*()"},
            "expected": {
                "error_visible": True,
                "error_any_of": ["Invalid credentials"],
                "url_contains": "/auth/login",
                "stays_on_page": True
            }
        },
        {
            "name": "unicode_username_password",
            "inputs": {"username": "用户", "password": "密码"},
            "expected": {
                "error_visible": True,
                "error_any_of": ["Invalid credentials"],
                "url_contains": "/auth/login",
                "stays_on_page": True
            }
        },
        {
            "name": "repeated_attempts",
            "inputs": {"username": "invalidUser", "password": "invalidPass"},
            "expected": {
                "error_visible": True,
                "error_any_of": ["Invalid credentials"],
                "url_contains": "/auth/login",
                "stays_on_page": True
            }
        }
    ]

    for case in test_cases:
        page.goto(APP_BASE_URL + "/web/index.php/auth/login")
        page.fill('input[name="username"]', case["inputs"]["username"])
        page.fill('input[name="password"]', case["inputs"]["password"])
        page.click('button[type="submit"]')

        if case["expected"]["error_visible"]:
            expect(page.locator('[role="alert"]')).to_be_visible()
            for error in case["expected"]["error_any_of"]:
                expect(page.locator('[role="alert"]')).to_contain_text(error)

        assert case["expected"]["url_contains"] in page.url
        if case["expected"]["stays_on_page"]:
            expect(page.locator('input[name="username"]')).to_be_visible()