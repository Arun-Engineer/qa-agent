"""
agent/tools/screenshot_capture.py — Page Screenshot Tool

Captures full-page or element-level screenshots using Playwright.
Returns base64-encoded images ready for GPT-4o vision API.

Usage:
    from agent.tools.screenshot_capture import capture_page, capture_element

    # Full page screenshot
    result = capture_page("https://example.com/products")

    # Element screenshot (e.g., product cards only)
    result = capture_element("https://example.com/products", selector=".product-card")
"""
from __future__ import annotations

import base64
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class ScreenshotResult:
    url: str
    status: str  # "ok" | "error"
    screenshots: List[Dict[str, Any]] = field(default_factory=list)
    # Each: {"label": "...", "base64": "...", "width": int, "height": int, "path": "..."}
    error: Optional[str] = None
    page_title: str = ""
    duration_ms: float = 0.0


def capture_page(
    url: str,
    viewport_width: int = 1440,
    viewport_height: int = 900,
    full_page: bool = True,
    wait_for: str = "networkidle",
    timeout_ms: int = 30000,
    output_dir: str = "data/screenshots",
    label: str = "",
) -> ScreenshotResult:
    """
    Capture a full-page screenshot.
    Returns ScreenshotResult with base64 image.
    """
    start = time.time()
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                viewport={"width": viewport_width, "height": viewport_height},
                ignore_https_errors=True,
            )
            page = context.new_page()

            page.goto(url, wait_until=wait_for, timeout=timeout_ms)
            page.wait_for_timeout(1000)  # Extra settle time for JS rendering

            title = page.title()

            # Generate filename
            slug = label or url.split("/")[-1] or "page"
            slug = "".join(c if c.isalnum() or c in "-_" else "_" for c in slug)[:50]
            ts = int(time.time())
            filename = f"{slug}_{ts}.png"
            filepath = out_dir / filename

            # Capture
            screenshot_bytes = page.screenshot(full_page=full_page)
            filepath.write_bytes(screenshot_bytes)

            b64 = base64.b64encode(screenshot_bytes).decode("utf-8")

            browser.close()

            elapsed = (time.time() - start) * 1000
            return ScreenshotResult(
                url=url,
                status="ok",
                page_title=title,
                duration_ms=round(elapsed, 2),
                screenshots=[{
                    "label": label or title or slug,
                    "base64": b64,
                    "path": str(filepath),
                    "width": viewport_width,
                    "height": 0,  # full_page height varies
                    "url": url,
                }],
            )

    except ImportError:
        return ScreenshotResult(
            url=url, status="error",
            error="Playwright not installed. Run: pip install playwright && playwright install chromium",
            duration_ms=(time.time() - start) * 1000,
        )
    except Exception as e:
        return ScreenshotResult(
            url=url, status="error", error=str(e),
            duration_ms=(time.time() - start) * 1000,
        )


def capture_multiple_pages(
    urls: List[Dict[str, str]],
    viewport_width: int = 1440,
    viewport_height: int = 900,
    output_dir: str = "data/screenshots",
) -> List[ScreenshotResult]:
    """
    Capture screenshots of multiple pages.
    urls: [{"url": "https://...", "label": "PLP"}, ...]
    """
    results = []
    for item in urls:
        url = item.get("url", "")
        label = item.get("label", "")
        if not url:
            continue
        result = capture_page(
            url=url, label=label,
            viewport_width=viewport_width,
            viewport_height=viewport_height,
            output_dir=output_dir,
        )
        results.append(result)
    return results


def capture_elements(
    url: str,
    selector: str,
    viewport_width: int = 1440,
    viewport_height: int = 900,
    max_elements: int = 20,
    output_dir: str = "data/screenshots",
    label_prefix: str = "element",
) -> ScreenshotResult:
    """
    Capture individual screenshots of matching elements on a page.
    Useful for: screenshot each product card, each row, etc.
    """
    start = time.time()
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                viewport={"width": viewport_width, "height": viewport_height},
                ignore_https_errors=True,
            )
            page = context.new_page()
            page.goto(url, wait_until="networkidle", timeout=30000)
            page.wait_for_timeout(1000)

            title = page.title()
            elements = page.query_selector_all(selector)[:max_elements]

            screenshots = []
            for i, el in enumerate(elements):
                try:
                    box = el.bounding_box()
                    if not box or box["width"] < 10 or box["height"] < 10:
                        continue

                    img_bytes = el.screenshot()
                    ts = int(time.time())
                    filename = f"{label_prefix}_{i}_{ts}.png"
                    filepath = out_dir / filename
                    filepath.write_bytes(img_bytes)

                    b64 = base64.b64encode(img_bytes).decode("utf-8")
                    text_content = el.inner_text()[:200] if el.inner_text() else ""

                    screenshots.append({
                        "label": f"{label_prefix}_{i}",
                        "base64": b64,
                        "path": str(filepath),
                        "width": int(box["width"]),
                        "height": int(box["height"]),
                        "text_content": text_content,
                        "url": url,
                    })
                except Exception:
                    continue

            browser.close()

            elapsed = (time.time() - start) * 1000
            return ScreenshotResult(
                url=url, status="ok", page_title=title,
                screenshots=screenshots,
                duration_ms=round(elapsed, 2),
            )

    except ImportError:
        return ScreenshotResult(
            url=url, status="error",
            error="Playwright not installed",
            duration_ms=(time.time() - start) * 1000,
        )
    except Exception as e:
        return ScreenshotResult(
            url=url, status="error", error=str(e),
            duration_ms=(time.time() - start) * 1000,
        )
