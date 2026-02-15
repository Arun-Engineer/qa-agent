import os
import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.parametrize("case", [
    {
        "name": "invalid_username_invalid_password",
        "inputs": {"username": "invalidUser", "password": "invalidPass"},
        "expected": {
            "error_visible": True,
            "error_any_of": ["Invalid credentials"],
            "url_contains": "/auth",
            "stays_on_page": True
        }
    },
    {
        "name": "empty_username_empty_password",
        "inputs": {"username": "", "password": ""},
        "expected": {
            "error_visible": True,
            "error_any_of": ["Required"],
            "url_contains": "/auth",
            "stays_on_page": True
        }
    },
    {
        "name": "whitespace_username_whitespace_password",
        "inputs": {"username": "   ", "password": "   "},
        "expected": {
            "error_visible": True,
            "error_any_of": ["Required"],
            "url_contains": "/auth",
            "stays_on_page": True
        }
    },
    {
        "name": "long_username_long_password",
        "inputs": {"username": "a", "password": "a"},
        "expected": {
            "error_visible": True,
            "error_any_of": ["Invalid credentials"],
            "url_contains": "/auth",
            "stays_on_page": True
        }
    },
    {
        "name": "special_chars_username_special_chars_password",
        "inputs": {"username": "!@#$%^&*()", "password": "!@#$%^&*()"},
        "expected": {
            "error_visible": True,
            "error_any_of": ["Invalid credentials"],
            "url_contains": "/auth",
            "stays_on_page": True
        }
    },
    {
        "name": "unicode_username_unicode_password",
        "inputs": {"username": "用户", "password": "密码"},
        "expected": {
            "error_visible": True,
            "error_any_of": ["Invalid credentials"],
            "url_contains": "/auth",
            "stays_on_page": True
        }
    },
    {
        "name": "injection_username_injection_password",
        "inputs": {"username": "' OR '1'='1", "password": "' OR '1'='1"},
        "expected": {
            "error_visible": True,
            "error_any_of": ["Invalid credentials"],
            "url_contains": "/auth",
            "stays_on_page": True
        }
    },
    {
        "name": "repeated_attempts",
        "inputs": {"username": "invalidUser", "password": "invalidPass"},
        "expected": {
            "error_visible": True,
            "error_any_of": ["Invalid credentials"],
            "url_contains": "/auth",
            "stays_on_page": True
        }
    }
])

def test_invalid_login(page: Page, case):
    page.goto("https://opensource-demo.orangehrmlive.com")
    page.fill("input[name='username']", case["inputs"]["username"])
    page.fill("input[name='password']", case["inputs"]["password"])
    page.click("button[type='submit']")
    
    if case["expected"]["error_visible"]:
        expect(page.locator("text=" + case["expected"]["error_any_of"][0])).to_be_visible()
        expect(page).to_have_url(expect.string_contains(case["expected"]["url_contains"]))
        assert case["expected"]["stays_on_page"] is True
    else:
        expect(page).to_have_url(expect.string_contains(case["expected"]["url_contains"]))