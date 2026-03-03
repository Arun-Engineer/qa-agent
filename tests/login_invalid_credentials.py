import re
import pytest
from playwright.sync_api import Page, expect

CASES = [
    {"name": "invalid_username_invalid_password", "inputs": {"username": "invalidUser", "password": "invalidPass"},
     "expected": {"error_visible": True, "error_any_of": ["Invalid credentials"]}},
    {"name": "empty_username_empty_password", "inputs": {"username": "", "password": ""},
     "expected": {"error_visible": True, "error_any_of": ["Invalid credentials", "Required"]}},
    {"name": "whitespace_username_whitespace_password", "inputs": {"username": "   ", "password": "   "},
     "expected": {"error_visible": True, "error_any_of": ["Invalid credentials", "Required"]}},
    {"name": "long_username_long_password", "inputs": {"username": "a", "password": "a"},
     "expected": {"error_visible": True, "error_any_of": ["Invalid credentials"]}},
    {"name": "special_chars_username_special_chars_password", "inputs": {"username": "!@#$%^&*()", "password": "!@#$%^&*()"},
     "expected": {"error_visible": True, "error_any_of": ["Invalid credentials"]}},
    {"name": "unicode_username_unicode_password", "inputs": {"username": "用户", "password": "密码"},
     "expected": {"error_visible": True, "error_any_of": ["Invalid credentials"]}},
    {"name": "injection_username_injection_password", "inputs": {"username": "' OR '1'='1", "password": "' OR '1'='1"},
     "expected": {"error_visible": True, "error_any_of": ["Invalid credentials"]}},
    {"name": "repeated_attempts", "inputs": {"username": "invalidUser", "password": "invalidPass"},
     "expected": {"error_visible": True, "error_any_of": ["Invalid credentials"]}},
]

@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_invalid_login(page: Page, case):
    page.goto("https://opensource-demo.orangehrmlive.com/web/index.php/auth/login")
    page.wait_for_load_state("networkidle")
    
    page.fill("input[name='username']", case["inputs"]["username"])
    page.fill("input[name='password']", case["inputs"]["password"])
    page.click("button[type='submit']")
    page.wait_for_timeout(1500)

    found_error = False
    for msg in case["expected"]["error_any_of"]:
        if page.get_by_text(msg).first.is_visible():
            found_error = True
            break
    assert found_error, f"Expected one of {case['expected']['error_any_of']} to be visible"
    
    expect(page).to_have_url(re.compile(r".*/auth/login.*"))