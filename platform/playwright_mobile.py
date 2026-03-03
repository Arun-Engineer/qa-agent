"""
Phase 5 · Playwright Mobile
15+ device profiles, touch gestures, touch-target validation,
responsive breakpoint testing, and mobile-specific assertions.
"""

import asyncio
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# Device Profiles — 15+ covering iOS, Android, tablets
# ═══════════════════════════════════════════════════════════════

@dataclass
class DeviceProfile:
    name: str
    user_agent: str
    viewport_width: int
    viewport_height: int
    device_scale_factor: float
    is_mobile: bool = True
    has_touch: bool = True
    default_browser: str = "webkit"  # "webkit" for iOS, "chromium" for Android


DEVICE_REGISTRY: Dict[str, DeviceProfile] = {
    # ── iPhones ────────────────────────────────────────────
    "iphone_se": DeviceProfile(
        name="iPhone SE", viewport_width=375, viewport_height=667,
        device_scale_factor=2.0, default_browser="webkit",
        user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
    ),
    "iphone_14": DeviceProfile(
        name="iPhone 14", viewport_width=390, viewport_height=844,
        device_scale_factor=3.0, default_browser="webkit",
        user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
    ),
    "iphone_14_pro_max": DeviceProfile(
        name="iPhone 14 Pro Max", viewport_width=430, viewport_height=932,
        device_scale_factor=3.0, default_browser="webkit",
        user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
    ),
    "iphone_15": DeviceProfile(
        name="iPhone 15", viewport_width=393, viewport_height=852,
        device_scale_factor=3.0, default_browser="webkit",
        user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1",
    ),
    "iphone_15_pro_max": DeviceProfile(
        name="iPhone 15 Pro Max", viewport_width=430, viewport_height=932,
        device_scale_factor=3.0, default_browser="webkit",
        user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1",
    ),
    # ── Android phones ─────────────────────────────────────
    "pixel_7": DeviceProfile(
        name="Pixel 7", viewport_width=412, viewport_height=915,
        device_scale_factor=2.625, default_browser="chromium",
        user_agent="Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
    ),
    "pixel_8_pro": DeviceProfile(
        name="Pixel 8 Pro", viewport_width=412, viewport_height=915,
        device_scale_factor=3.5, default_browser="chromium",
        user_agent="Mozilla/5.0 (Linux; Android 14; Pixel 8 Pro) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
    ),
    "samsung_s23": DeviceProfile(
        name="Samsung Galaxy S23", viewport_width=360, viewport_height=780,
        device_scale_factor=3.0, default_browser="chromium",
        user_agent="Mozilla/5.0 (Linux; Android 14; SM-S911B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
    ),
    "samsung_s24_ultra": DeviceProfile(
        name="Samsung Galaxy S24 Ultra", viewport_width=412, viewport_height=915,
        device_scale_factor=3.5, default_browser="chromium",
        user_agent="Mozilla/5.0 (Linux; Android 14; SM-S928B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
    ),
    "oneplus_12": DeviceProfile(
        name="OnePlus 12", viewport_width=412, viewport_height=915,
        device_scale_factor=3.5, default_browser="chromium",
        user_agent="Mozilla/5.0 (Linux; Android 14; CPH2583) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
    ),
    # ── Tablets ────────────────────────────────────────────
    "ipad_10th": DeviceProfile(
        name="iPad 10th Gen", viewport_width=820, viewport_height=1180,
        device_scale_factor=2.0, default_browser="webkit", is_mobile=False,
        user_agent="Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
    ),
    "ipad_pro_12": DeviceProfile(
        name="iPad Pro 12.9\"", viewport_width=1024, viewport_height=1366,
        device_scale_factor=2.0, default_browser="webkit", is_mobile=False,
        user_agent="Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
    ),
    "ipad_mini": DeviceProfile(
        name="iPad Mini 6th", viewport_width=744, viewport_height=1133,
        device_scale_factor=2.0, default_browser="webkit", is_mobile=False,
        user_agent="Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
    ),
    "galaxy_tab_s9": DeviceProfile(
        name="Galaxy Tab S9", viewport_width=800, viewport_height=1280,
        device_scale_factor=2.0, default_browser="chromium", is_mobile=False,
        user_agent="Mozilla/5.0 (Linux; Android 14; SM-X710B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    ),
    "surface_pro_9": DeviceProfile(
        name="Surface Pro 9", viewport_width=1368, viewport_height=912,
        device_scale_factor=2.0, default_browser="chromium", is_mobile=False,
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    ),
}


@dataclass
class TouchTargetViolation:
    """WCAG 2.5.8 touch target violation."""
    selector: str
    element_tag: str
    width_px: float
    height_px: float
    min_required_px: float = 44.0  # WCAG AAA = 44px; AA = 24px
    text_content: str = ""
    bounding_box: Dict[str, float] = field(default_factory=dict)


@dataclass
class MobileTestResult:
    """Result from a mobile device test run."""
    device: str
    url: str
    passed: bool = True
    actions: List[Any] = field(default_factory=list)
    touch_violations: List[TouchTargetViolation] = field(default_factory=list)
    viewport_overflow: bool = False
    video_path: Optional[str] = None
    trace_path: Optional[str] = None
    duration_ms: float = 0.0
    console_errors: List[str] = field(default_factory=list)


class PlaywrightMobile:
    """
    Mobile web testing with 15+ device profiles,
    touch-target validation, and responsive checks.
    """

    def __init__(
        self,
        device_key: str = "iphone_14",
        headless: bool = True,
        record_video: bool = True,
        record_trace: bool = True,
        artifacts_dir: str = "/tmp/pw_mobile_artifacts",
        default_timeout: int = 30_000,
    ):
        if device_key not in DEVICE_REGISTRY:
            raise ValueError(
                f"Unknown device '{device_key}'. Available: {list(DEVICE_REGISTRY.keys())}"
            )
        self.device_key = device_key
        self.profile = DEVICE_REGISTRY[device_key]
        self.headless = headless
        self.record_video = record_video
        self.record_trace = record_trace
        self.artifacts_dir = artifacts_dir
        self.default_timeout = default_timeout

        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._console_errors: List[str] = []

    # ── lifecycle ──────────────────────────────────────────────

    async def launch(self) -> "PlaywrightMobile":
        from playwright.async_api import async_playwright

        self._playwright = await async_playwright().start()
        browser_type = getattr(self._playwright, self.profile.default_browser)
        self._browser = await browser_type.launch(headless=self.headless)

        artifacts = os.path.join(self.artifacts_dir, self.device_key)
        os.makedirs(artifacts, exist_ok=True)

        ctx_opts = {
            "viewport": {
                "width": self.profile.viewport_width,
                "height": self.profile.viewport_height,
            },
            "device_scale_factor": self.profile.device_scale_factor,
            "is_mobile": self.profile.is_mobile,
            "has_touch": self.profile.has_touch,
            "user_agent": self.profile.user_agent,
        }
        if self.record_video:
            ctx_opts["record_video_dir"] = os.path.join(artifacts, "videos")
            ctx_opts["record_video_size"] = {
                "width": self.profile.viewport_width,
                "height": self.profile.viewport_height,
            }

        self._context = await self._browser.new_context(**ctx_opts)
        self._context.set_default_timeout(self.default_timeout)

        if self.record_trace:
            await self._context.tracing.start(screenshots=True, snapshots=True)

        self._page = await self._context.new_page()
        self._page.on("console", lambda msg: (
            self._console_errors.append(f"[{msg.type}] {msg.text}")
            if msg.type in ("error", "warning") else None
        ))

        logger.info("Launched mobile: %s (%dx%d @ %.1fx)",
                     self.profile.name, self.profile.viewport_width,
                     self.profile.viewport_height, self.profile.device_scale_factor)
        return self

    async def close(self) -> Dict[str, Optional[str]]:
        artifacts: Dict[str, Optional[str]] = {"video": None, "trace": None}
        try:
            if self.record_trace and self._context:
                tp = os.path.join(self.artifacts_dir, self.device_key, f"trace-{self.device_key}.zip")
                await self._context.tracing.stop(path=tp)
                artifacts["trace"] = tp
            if self._page and self.record_video and self._page.video:
                artifacts["video"] = str(await self._page.video.path())
            if self._context:
                await self._context.close()
            if self._browser:
                await self._browser.close()
            if self._playwright:
                await self._playwright.stop()
        except Exception as e:
            logger.warning("Mobile close error: %s", e)
        return artifacts

    # ── navigation & actions ───────────────────────────────────

    async def goto(self, url: str) -> None:
        await self._page.goto(url, wait_until="domcontentloaded")

    async def tap(self, selector: str) -> None:
        await self._page.locator(selector).tap()

    async def swipe(
        self, start: Tuple[int, int], end: Tuple[int, int], duration_ms: int = 300,
    ) -> None:
        """Simulate a swipe gesture via touchscreen."""
        ts = self._page.touchscreen
        await ts.tap(start[0], start[1])  # press
        steps = max(duration_ms // 16, 5)
        dx = (end[0] - start[0]) / steps
        dy = (end[1] - start[1]) / steps
        for i in range(1, steps + 1):
            await self._page.mouse.move(start[0] + dx * i, start[1] + dy * i)
            await asyncio.sleep(0.016)

    async def scroll_down(self, pixels: int = 500) -> None:
        vw = self.profile.viewport_width
        vh = self.profile.viewport_height
        await self.swipe((vw // 2, vh // 2), (vw // 2, vh // 2 - pixels))

    async def scroll_up(self, pixels: int = 500) -> None:
        vw = self.profile.viewport_width
        vh = self.profile.viewport_height
        await self.swipe((vw // 2, vh // 2), (vw // 2, vh // 2 + pixels))

    async def pinch_zoom(self, scale: float = 2.0) -> None:
        """Emulate pinch-zoom via cdp (Chromium only)."""
        try:
            cdp = await self._page.context.new_cdp_session(self._page)
            cx = self.profile.viewport_width // 2
            cy = self.profile.viewport_height // 2
            await cdp.send("Input.synthesizePinchGesture", {
                "x": cx, "y": cy, "scaleFactor": scale, "relativeSpeed": 300,
            })
        except Exception as e:
            logger.warning("Pinch zoom not supported: %s", e)

    async def rotate(self, landscape: bool = True) -> None:
        """Switch between portrait and landscape."""
        if landscape:
            w, h = self.profile.viewport_height, self.profile.viewport_width
        else:
            w, h = self.profile.viewport_width, self.profile.viewport_height
        await self._page.set_viewport_size({"width": w, "height": h})

    # ── touch target validation ────────────────────────────────

    async def validate_touch_targets(
        self,
        min_size_px: float = 44.0,
        selectors: str = "a, button, input, select, textarea, [role='button'], [onclick]",
    ) -> List[TouchTargetViolation]:
        """
        WCAG 2.5.8: all interactive elements must be at least min_size_px.
        Returns a list of violations.
        """
        violations = []
        elements = await self._page.query_selector_all(selectors)
        for el in elements:
            box = await el.bounding_box()
            if not box:
                continue
            tag = await el.evaluate("e => e.tagName.toLowerCase()")
            text = (await el.inner_text()).strip()[:60] if await el.inner_text() else ""

            if box["width"] < min_size_px or box["height"] < min_size_px:
                violations.append(TouchTargetViolation(
                    selector=f"{tag}",
                    element_tag=tag,
                    width_px=round(box["width"], 1),
                    height_px=round(box["height"], 1),
                    min_required_px=min_size_px,
                    text_content=text,
                    bounding_box=box,
                ))
        logger.info("Touch target audit: %d elements, %d violations", len(elements), len(violations))
        return violations

    # ── viewport overflow detection ────────────────────────────

    async def check_viewport_overflow(self) -> bool:
        """Check if any content overflows the viewport horizontally."""
        overflow = await self._page.evaluate("""
            () => {
                const docWidth = document.documentElement.scrollWidth;
                const viewWidth = window.innerWidth;
                return docWidth > viewWidth;
            }
        """)
        return bool(overflow)

    # ── screenshot ─────────────────────────────────────────────

    async def screenshot(self, name: Optional[str] = None, full_page: bool = False) -> str:
        name = name or f"{self.device_key}"
        path = os.path.join(self.artifacts_dir, self.device_key, "screenshots", f"{name}.png")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        await self._page.screenshot(path=path, full_page=full_page)
        return path

    # ── multi-device matrix ────────────────────────────────────

    @staticmethod
    async def run_device_matrix(
        test_fn: Callable,
        url: str,
        device_keys: Optional[List[str]] = None,
        touch_target_audit: bool = True,
        artifacts_dir: str = "/tmp/pw_mobile_artifacts",
    ) -> List[MobileTestResult]:
        """
        Run a test across multiple device profiles.
        test_fn: async def test(mobile: PlaywrightMobile) -> List[ActionResult]
        """
        if device_keys is None:
            device_keys = list(DEVICE_REGISTRY.keys())

        results = []
        for dk in device_keys:
            import time
            start = time.monotonic()
            mobile = PlaywrightMobile(
                device_key=dk, artifacts_dir=artifacts_dir,
            )
            result = MobileTestResult(device=dk, url=url)
            try:
                await mobile.launch()
                await mobile.goto(url)
                actions = await test_fn(mobile)
                result.actions = actions

                if touch_target_audit:
                    result.touch_violations = await mobile.validate_touch_targets()

                result.viewport_overflow = await mobile.check_viewport_overflow()
                result.passed = (
                    all(getattr(a, "success", True) for a in actions)
                    and len(result.touch_violations) == 0
                    and not result.viewport_overflow
                )
            except Exception as e:
                logger.error("Device %s failed: %s", dk, e)
                result.passed = False
            finally:
                result.console_errors = mobile._console_errors.copy()
                arts = await mobile.close()
                result.video_path = arts.get("video")
                result.trace_path = arts.get("trace")
                result.duration_ms = (time.monotonic() - start) * 1000
                results.append(result)

            logger.info("Device %s → %s (%.0fms, %d touch violations)",
                        dk, "PASS" if result.passed else "FAIL",
                        result.duration_ms, len(result.touch_violations))
        return results