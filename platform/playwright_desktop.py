"""
Phase 5 · Playwright Desktop
Cross-browser testing: Chrome, Firefox, Safari at 3 viewports.
Video recording, trace capture, screenshot-on-failure, network interception.
"""

import asyncio
import logging
import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class Browser(Enum):
    CHROMIUM = "chromium"
    FIREFOX = "firefox"
    WEBKIT = "webkit"


class Viewport(Enum):
    DESKTOP_LARGE = (1920, 1080)
    DESKTOP_MEDIUM = (1366, 768)
    DESKTOP_SMALL = (1024, 768)

    @property
    def width(self) -> int:
        return self.value[0]

    @property
    def height(self) -> int:
        return self.value[1]


@dataclass
class BrowserConfig:
    """Configuration for a desktop browser test session."""
    browser: Browser = Browser.CHROMIUM
    viewport: Viewport = Viewport.DESKTOP_LARGE
    headless: bool = True
    slow_mo: int = 0  # ms between actions
    locale: str = "en-US"
    timezone: str = "America/New_York"
    color_scheme: str = "light"  # "light" | "dark" | "no-preference"
    record_video: bool = True
    record_trace: bool = True
    screenshot_on_failure: bool = True
    base_url: Optional[str] = None
    extra_http_headers: Dict[str, str] = field(default_factory=dict)
    ignore_https_errors: bool = False
    proxy: Optional[Dict[str, str]] = None
    downloads_path: str = "/tmp/pw_downloads"
    artifacts_dir: str = "/tmp/pw_artifacts"
    default_timeout: int = 30_000  # ms
    default_navigation_timeout: int = 60_000  # ms


@dataclass
class ActionResult:
    """Result of a single browser action."""
    action: str
    selector: Optional[str] = None
    success: bool = True
    duration_ms: float = 0.0
    screenshot_path: Optional[str] = None
    error: Optional[str] = None
    extras: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TestRunResult:
    """Aggregated result of a full test run."""
    browser: Browser
    viewport: Viewport
    url: str
    passed: bool = True
    actions: List[ActionResult] = field(default_factory=list)
    video_path: Optional[str] = None
    trace_path: Optional[str] = None
    console_errors: List[str] = field(default_factory=list)
    network_failures: List[Dict[str, Any]] = field(default_factory=list)
    duration_ms: float = 0.0


class PlaywrightDesktop:
    """
    Cross-browser desktop test runner using Playwright.
    Supports Chrome, Firefox, Safari across 3 standard viewports.
    """

    def __init__(self, config: Optional[BrowserConfig] = None):
        self.config = config or BrowserConfig()
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._console_errors: List[str] = []
        self._network_failures: List[Dict[str, Any]] = []

    # ── lifecycle ──────────────────────────────────────────────

    async def launch(self) -> "PlaywrightDesktop":
        """Start Playwright and launch browser with configured options."""
        from playwright.async_api import async_playwright

        self._playwright = await async_playwright().start()
        browser_type = getattr(self._playwright, self.config.browser.value)

        launch_opts: Dict[str, Any] = {
            "headless": self.config.headless,
            "slow_mo": self.config.slow_mo,
        }
        if self.config.proxy:
            launch_opts["proxy"] = self.config.proxy

        self._browser = await browser_type.launch(**launch_opts)

        # artifacts directory
        artifacts = Path(self.config.artifacts_dir)
        artifacts.mkdir(parents=True, exist_ok=True)

        # context with viewport, video, trace
        ctx_opts: Dict[str, Any] = {
            "viewport": {
                "width": self.config.viewport.width,
                "height": self.config.viewport.height,
            },
            "locale": self.config.locale,
            "timezone_id": self.config.timezone,
            "color_scheme": self.config.color_scheme,
            "ignore_https_errors": self.config.ignore_https_errors,
            "extra_http_headers": self.config.extra_http_headers,
        }
        if self.config.base_url:
            ctx_opts["base_url"] = self.config.base_url
        if self.config.record_video:
            ctx_opts["record_video_dir"] = str(artifacts / "videos")
            ctx_opts["record_video_size"] = {
                "width": self.config.viewport.width,
                "height": self.config.viewport.height,
            }

        self._context = await self._browser.new_context(**ctx_opts)
        self._context.set_default_timeout(self.config.default_timeout)
        self._context.set_default_navigation_timeout(self.config.default_navigation_timeout)

        if self.config.record_trace:
            await self._context.tracing.start(
                screenshots=True, snapshots=True, sources=True,
            )

        self._page = await self._context.new_page()
        self._attach_listeners()

        logger.info(
            "Launched %s @ %dx%d (headless=%s)",
            self.config.browser.value,
            self.config.viewport.width,
            self.config.viewport.height,
            self.config.headless,
        )
        return self

    async def close(self) -> Dict[str, Optional[str]]:
        """Stop tracing, save artifacts, close browser."""
        artifacts: Dict[str, Optional[str]] = {
            "video": None, "trace": None,
        }
        try:
            if self.config.record_trace and self._context:
                trace_path = os.path.join(
                    self.config.artifacts_dir, "traces",
                    f"trace-{self.config.browser.value}-{self.config.viewport.name}.zip",
                )
                os.makedirs(os.path.dirname(trace_path), exist_ok=True)
                await self._context.tracing.stop(path=trace_path)
                artifacts["trace"] = trace_path

            if self._page and self.config.record_video:
                video = self._page.video
                if video:
                    video_path = await video.path()
                    artifacts["video"] = str(video_path)

            if self._context:
                await self._context.close()
            if self._browser:
                await self._browser.close()
            if self._playwright:
                await self._playwright.stop()
        except Exception as e:
            logger.warning("Error during close: %s", e)

        return artifacts

    # ── navigation ─────────────────────────────────────────────

    async def goto(self, url: str, wait_until: str = "domcontentloaded") -> ActionResult:
        """Navigate to a URL."""
        import time
        start = time.monotonic()
        try:
            resp = await self._page.goto(url, wait_until=wait_until)
            elapsed = (time.monotonic() - start) * 1000
            status = resp.status if resp else 0
            return ActionResult(
                action="goto", success=200 <= status < 400,
                duration_ms=elapsed,
                extras={"status": status, "url": url},
            )
        except Exception as e:
            elapsed = (time.monotonic() - start) * 1000
            return self._fail("goto", None, elapsed, str(e))

    async def reload(self) -> ActionResult:
        import time
        start = time.monotonic()
        try:
            await self._page.reload(wait_until="domcontentloaded")
            return ActionResult(action="reload", duration_ms=(time.monotonic() - start) * 1000)
        except Exception as e:
            return self._fail("reload", None, (time.monotonic() - start) * 1000, str(e))

    # ── interactions ───────────────────────────────────────────

    async def click(self, selector: str, **kwargs) -> ActionResult:
        return await self._action("click", selector, **kwargs)

    async def fill(self, selector: str, value: str, **kwargs) -> ActionResult:
        return await self._action("fill", selector, value=value, **kwargs)

    async def type_text(self, selector: str, text: str, delay: int = 50) -> ActionResult:
        return await self._action("type", selector, text=text, delay=delay)

    async def select_option(self, selector: str, value: str) -> ActionResult:
        return await self._action("select_option", selector, value=value)

    async def check(self, selector: str) -> ActionResult:
        return await self._action("check", selector)

    async def uncheck(self, selector: str) -> ActionResult:
        return await self._action("uncheck", selector)

    async def hover(self, selector: str) -> ActionResult:
        return await self._action("hover", selector)

    async def press(self, selector: str, key: str) -> ActionResult:
        return await self._action("press", selector, key=key)

    async def drag_and_drop(self, source: str, target: str) -> ActionResult:
        import time
        start = time.monotonic()
        try:
            await self._page.drag_and_drop(source, target)
            return ActionResult(
                action="drag_and_drop", selector=f"{source} → {target}",
                duration_ms=(time.monotonic() - start) * 1000,
            )
        except Exception as e:
            return self._fail("drag_and_drop", source, (time.monotonic() - start) * 1000, str(e))

    async def upload_file(self, selector: str, file_path: str) -> ActionResult:
        import time
        start = time.monotonic()
        try:
            await self._page.set_input_files(selector, file_path)
            return ActionResult(
                action="upload_file", selector=selector,
                duration_ms=(time.monotonic() - start) * 1000,
            )
        except Exception as e:
            return self._fail("upload_file", selector, (time.monotonic() - start) * 1000, str(e))

    # ── waiting & assertions ───────────────────────────────────

    async def wait_for_selector(
        self, selector: str, state: str = "visible", timeout: int = 10_000,
    ) -> ActionResult:
        import time
        start = time.monotonic()
        try:
            await self._page.wait_for_selector(selector, state=state, timeout=timeout)
            return ActionResult(
                action="wait_for_selector", selector=selector,
                duration_ms=(time.monotonic() - start) * 1000,
            )
        except Exception as e:
            return self._fail("wait_for_selector", selector, (time.monotonic() - start) * 1000, str(e))

    async def wait_for_url(self, url_pattern: str, timeout: int = 10_000) -> ActionResult:
        import time
        start = time.monotonic()
        try:
            await self._page.wait_for_url(url_pattern, timeout=timeout)
            return ActionResult(
                action="wait_for_url", duration_ms=(time.monotonic() - start) * 1000,
                extras={"pattern": url_pattern},
            )
        except Exception as e:
            return self._fail("wait_for_url", None, (time.monotonic() - start) * 1000, str(e))

    async def assert_text_visible(self, text: str, timeout: int = 5_000) -> ActionResult:
        import time
        start = time.monotonic()
        try:
            locator = self._page.get_by_text(text)
            await locator.first.wait_for(state="visible", timeout=timeout)
            return ActionResult(
                action="assert_text_visible", selector=f"text={text}",
                duration_ms=(time.monotonic() - start) * 1000,
            )
        except Exception as e:
            return self._fail("assert_text_visible", f"text={text}",
                              (time.monotonic() - start) * 1000, str(e))

    # ── screenshots ────────────────────────────────────────────

    async def screenshot(self, name: str = "screenshot", full_page: bool = False) -> str:
        path = os.path.join(self.config.artifacts_dir, "screenshots", f"{name}.png")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        await self._page.screenshot(path=path, full_page=full_page)
        return path

    async def screenshot_element(self, selector: str, name: str = "element") -> str:
        path = os.path.join(self.config.artifacts_dir, "screenshots", f"{name}.png")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        el = self._page.locator(selector)
        await el.screenshot(path=path)
        return path

    # ── JavaScript evaluation ──────────────────────────────────

    async def evaluate(self, expression: str) -> Any:
        return await self._page.evaluate(expression)

    async def evaluate_handle(self, expression: str) -> Any:
        return await self._page.evaluate_handle(expression)

    # ── network interception ───────────────────────────────────

    async def intercept_route(
        self, url_pattern: str, handler: Callable,
    ) -> None:
        await self._page.route(url_pattern, handler)

    async def block_resources(self, resource_types: List[str]) -> None:
        """Block specific resource types (e.g., 'image', 'font', 'stylesheet')."""
        async def _block(route):
            if route.request.resource_type in resource_types:
                await route.abort()
            else:
                await route.continue_()
        await self._page.route("**/*", _block)

    # ── multi-browser matrix run ───────────────────────────────

    @staticmethod
    async def run_matrix(
        test_fn: Callable,
        url: str,
        browsers: Optional[List[Browser]] = None,
        viewports: Optional[List[Viewport]] = None,
        base_config: Optional[BrowserConfig] = None,
    ) -> List[TestRunResult]:
        """
        Execute a test function across a browser × viewport matrix.

        test_fn signature: async def test(desktop: PlaywrightDesktop) -> List[ActionResult]
        """
        browsers = browsers or [Browser.CHROMIUM, Browser.FIREFOX, Browser.WEBKIT]
        viewports = viewports or [Viewport.DESKTOP_LARGE, Viewport.DESKTOP_MEDIUM, Viewport.DESKTOP_SMALL]
        base = base_config or BrowserConfig()
        results: List[TestRunResult] = []

        for browser in browsers:
            for viewport in viewports:
                cfg = BrowserConfig(
                    browser=browser,
                    viewport=viewport,
                    headless=base.headless,
                    slow_mo=base.slow_mo,
                    locale=base.locale,
                    timezone=base.timezone,
                    color_scheme=base.color_scheme,
                    record_video=base.record_video,
                    record_trace=base.record_trace,
                    screenshot_on_failure=base.screenshot_on_failure,
                    base_url=base.base_url,
                    extra_http_headers=base.extra_http_headers,
                    ignore_https_errors=base.ignore_https_errors,
                    artifacts_dir=os.path.join(
                        base.artifacts_dir, f"{browser.value}_{viewport.name}"
                    ),
                    default_timeout=base.default_timeout,
                    default_navigation_timeout=base.default_navigation_timeout,
                )
                desktop = PlaywrightDesktop(cfg)
                import time
                run_start = time.monotonic()
                run_result = TestRunResult(
                    browser=browser, viewport=viewport, url=url,
                )
                try:
                    await desktop.launch()
                    nav = await desktop.goto(url)
                    run_result.actions.append(nav)

                    actions = await test_fn(desktop)
                    run_result.actions.extend(actions)

                    run_result.passed = all(a.success for a in run_result.actions)
                except Exception as e:
                    logger.error("Matrix run %s/%s failed: %s", browser.value, viewport.name, e)
                    run_result.passed = False
                    run_result.actions.append(ActionResult(
                        action="matrix_error", success=False, error=str(e),
                    ))
                finally:
                    if not run_result.passed and cfg.screenshot_on_failure:
                        try:
                            ss = await desktop.screenshot(
                                f"failure-{browser.value}-{viewport.name}"
                            )
                            run_result.actions[-1].screenshot_path = ss
                        except Exception:
                            pass
                    run_result.console_errors = desktop._console_errors.copy()
                    run_result.network_failures = desktop._network_failures.copy()
                    arts = await desktop.close()
                    run_result.video_path = arts.get("video")
                    run_result.trace_path = arts.get("trace")
                    run_result.duration_ms = (time.monotonic() - run_start) * 1000

                results.append(run_result)
                logger.info(
                    "Matrix %s/%s → %s (%.0fms)",
                    browser.value, viewport.name,
                    "PASS" if run_result.passed else "FAIL",
                    run_result.duration_ms,
                )

        return results

    # ── accessibility audit ────────────────────────────────────

    async def accessibility_snapshot(self) -> Dict[str, Any]:
        """Return the accessibility tree for the current page."""
        return await self._page.accessibility.snapshot()

    # ── internals ──────────────────────────────────────────────

    def _attach_listeners(self):
        self._page.on("console", self._on_console)
        self._page.on("requestfailed", self._on_request_failed)

    def _on_console(self, msg):
        if msg.type in ("error", "warning"):
            self._console_errors.append(f"[{msg.type}] {msg.text}")

    def _on_request_failed(self, request):
        self._network_failures.append({
            "url": request.url,
            "method": request.method,
            "failure": request.failure,
            "resource_type": request.resource_type,
        })

    async def _action(self, method: str, selector: str, **kwargs) -> ActionResult:
        import time
        start = time.monotonic()
        try:
            locator = self._page.locator(selector)
            fn = getattr(locator, method)
            await fn(**kwargs)
            elapsed = (time.monotonic() - start) * 1000
            return ActionResult(action=method, selector=selector, duration_ms=elapsed)
        except Exception as e:
            elapsed = (time.monotonic() - start) * 1000
            result = self._fail(method, selector, elapsed, str(e))
            if self.config.screenshot_on_failure:
                try:
                    result.screenshot_path = await self.screenshot(f"fail-{method}-{int(start)}")
                except Exception:
                    pass
            return result

    @staticmethod
    def _fail(action: str, selector: Optional[str], duration_ms: float, error: str) -> ActionResult:
        return ActionResult(
            action=action, selector=selector,
            success=False, duration_ms=duration_ms, error=error,
        )