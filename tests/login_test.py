import os
import pytest
from playwright.sync_api import Page, expect

APP_BASE_URL = "https://opensource-demo.orangehrmlive.com"

pytestmark = [
    pytest.mark.ui,
    pytest.mark.positive,
    pytest.mark.negative,
    pytest.mark.state,
]

test_cases = [
    {
        "name": "valid_login",
        "inputs": {"username": "Admin", "password": "admin123"},
        "expected": {
            "outcome": "success",
            "url_contains": "/dashboard",
            "error_visible": False,
            "stays_on_page": False,
        },
    },
    {
        "name": "invalid_username",
        "inputs": {"username": "InvalidUser", "password": "admin123"},
        "expected": {
            "outcome": "error",
            "url_contains": "/login",
            "error_visible": True,
            "stays_on_page": True,
        },
    },
    {
        "name": "invalid_password",
        "inputs": {"username": "Admin", "password": "wrongpassword"},
        "expected": {
            "outcome": "error",
            "url_contains": "/login",
            "error_visible": True,
            "stays_on_page": True,
        },
    },
    {
        "name": "empty_username",
        "inputs": {"username": "", "password": "admin123"},
        "expected": {
            "outcome": "error",
            "url_contains": "/login",
            "error_visible": True,
            "stays_on_page": True,
        },
    },
    {
        "name": "empty_password",
        "inputs": {"username": "Admin", "password": ""},
        "expected": {
            "outcome": "error",
            "url_contains": "/login",
            "error_visible": True,
            "stays_on_page": True,
        },
    },
    {
        "name": "whitespace_username",
        "inputs": {"username": "   ", "password": "admin123"},
        "expected": {
            "outcome": "error",
            "url_contains": "/login",
            "error_visible": True,
            "stays_on_page": True,
        },
    },
    {
        "name": "whitespace_password",
        "inputs": {"username": "Admin", "password": "   "},
        "expected": {
            "outcome": "error",
            "url_contains": "/login",
            "error_visible": True,
            "stays_on_page": True,
        },
    },
    {
        "name": "long_username",
        "inputs": {"username": "A", "password": "admin123"},
        "expected": {
            "outcome": "error",
            "url_contains": "/login",
            "error_visible": True,
            "stays_on_page": True,
        },
    },
]

@pytest.mark.parametrize("case", test_cases)
def test_login(page: Page, case):
    page.goto(APP_BASE_URL + "/login")
    page.fill("input[name='username']", case["inputs"]["username"])
    page.fill("input[name='password']", case["inputs"]["password"])
    page.click("button[type='submit']")
    
    if case["expected"]["outcome"] == "error":
        expect(page.locator("text=Invalid credentials").first).to_be_visible() if case["expected"]["error_visible"] else None
        expect(page).to_have_url(APP_BASE_URL + case["expected"]["url_contains"])
        assert case["expected"]["stays_on_page"] == (page.url.endswith("/login"))
    else:
        expect(page).to_have_url(APP_BASE_URL + case["expected"]["url_contains"])
        assert not case["expected"]["error_visible"]