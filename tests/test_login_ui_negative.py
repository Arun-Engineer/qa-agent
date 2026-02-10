# Auto-generated test
```python
import pytest
from playwright.async_api import async_playwright

@pytest.mark.ui
@pytest.mark.asyncio
async def test_invalid_login_ui():
    base_url = "https://example.com"
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, slow_mo=100)
        context = await browser.new_context(record_video_dir="videos/", record_har_path="trace.har")
        page = await context.new_page()

        try:
            await page.goto(f"{base_url}/login", wait_until="domcontentloaded")
            await page.fill("#email", "invalid@example.com")
            await page.fill("#password", "wrongpass")
            await page.click("#submit")
            assert await page.locator(".toast-error, .error, [role='alert']").first.is_visible()
        except Exception:
            await page.screenshot(path="screenshots/failed_login.png")
            raise
        finally:
            await context.close()
            await browser.close()
```