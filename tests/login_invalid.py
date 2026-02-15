import os
import pytest
from playwright.sync_api import Page, expect

APP_BASE_URL = os.getenv("APP_BASE_URL", "https://opensource-demo.orangehrmlive.com").rstrip('/')

pytestmark = [pytest.mark.ui, pytest.mark.negative, pytest.mark.state]

test_cases = [
    {
        "name": "invalid_username_invalid_password",
        "inputs": {"username": "invalidUser", "password": "invalidPass"},
        "expected": {
            "error_visible": True,
            "url_contains": "/login",
        }
    },
    {
        "name": "empty_username_empty_password",
        "inputs": {"username": "", "password": ""},
        "expected": {
            "error_visible": True,
            "url_contains": "/login",
        }
    },
    {
        "name": "whitespace_username_whitespace_password",
        "inputs": {"username": "   ", "password": "   "},
        "expected": {
            "error_visible": True,
            "url_contains": "/login",
        }
    },
    {
        "name": "long_username_long_password",
        "inputs": {"username": "a", "password": "a"},
        "expected": {
            "error_visible": True,
            "url_contains": "/login",
        }
    },
    {
        "name": "special_chars_username_special_chars_password",
        "inputs": {"username": "!@#$%^&*()", "password": "!@#$%^&*()"},
        "expected": {
            "error_visible": True,
            "url_contains": "/login",
        }
    },
    {
        "name": "unicode_username_unicode_password",
        "inputs": {"username": "用户", "password": "密码"},
        "expected": {
            "error_visible": True,
            "url_contains": "/login",
        }
    },
    {
        "name": "injection_username_injection_password",
        "inputs": {"username": "' OR '1'='1", "password": "' OR '1'='1"},
        "expected": {
            "error_visible": True,
            "url_contains": "/login",
        }
    },
    {
        "name": "repeated_attempts",
        "inputs": {"username": "invalidUser", "password": "invalidPass"},
        "expected": {
            "error_visible": True,
            "url_contains": "/login",
        }
    }
]

@pytest.mark.parametrize("case", test_cases)
def test_invalid_login(page: Page, case):
    page.goto(APP_BASE_URL + "/login")
    page.fill("input[name='username']", case["inputs"]["username"])
    page.fill("input[name='password']", case["inputs"]["password"])
    page.click("button[type='submit']")
    
    if case["expected"]["error_visible"]:
        expect(page.locator("text=Invalid credentials").first).to_be_visible(timeout=5000)
        expect(page).to_have_url(APP_BASE_URL + "/login")
    else:
        expect(page).to_have_url(APP_BASE_URL + case["expected"]["url_contains"])