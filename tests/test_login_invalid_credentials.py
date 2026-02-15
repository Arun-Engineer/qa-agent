import os
import pytest
from pathlib import Path
from playwright.sync_api import Page, expect

APP_BASE_URL = os.getenv("APP_BASE_URL", "https://opensource-demo.orangehrmlive.com")

def _ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)

@pytest.mark.ui
@pytest.mark.login
@pytest.mark.negative
@pytest.mark.error
@pytest.mark.domain_hr
def test_login_invalid_credentials(page: Page):
    """Attempt to login with invalid username and password and assert error message."""
    
    # Navigate to the login page
    page.goto(APP_BASE_URL + "/web/index.php/auth/login")
    
    # Fill in the login form with invalid credentials
    page.fill('input[name="username"]', 'invalidUser')
    page.fill('input[name="password"]', 'invalidPass')
    
    # Click the login button
    page.click('button[type="submit"]')
    
    # Assert that the error message is visible
    expect(page.locator('[role="alert"], .error')).to_be_visible()
    expect(page.locator('[role="alert"], .error')).to_have_text("Invalid credentials")  # Adjust based on actual error message

    # Take a screenshot on failure
    if not page.locator('[role="alert"], .error').is_visible():
        page.screenshot(path=f'logs/screenshots/test_login_invalid_credentials.png')