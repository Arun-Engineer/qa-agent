"""
Phase 5 · Appium Native
Appium 2.0 driver for native iOS/Android testing.
Supports: push notifications, biometric auth, camera injection,
offline mode, deep links, and gesture chains.
"""

import asyncio
import base64
import json
import logging
import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class Platform(Enum):
    IOS = "ios"
    ANDROID = "android"


class BiometricType(Enum):
    FACE_ID = "faceId"
    TOUCH_ID = "touchId"
    FINGERPRINT = "fingerprint"  # Android


class NetworkCondition(Enum):
    WIFI = "wifi"
    CELLULAR = "cellular"
    AIRPLANE = "airplane"
    OFFLINE = "offline"
    FULL = "full"


@dataclass
class AppiumConfig:
    """Appium 2.0 session capabilities."""
    platform: Platform = Platform.ANDROID
    device_name: str = "emulator-5554"
    platform_version: str = "14"
    app_path: Optional[str] = None
    app_package: Optional[str] = None  # Android
    app_activity: Optional[str] = None  # Android
    bundle_id: Optional[str] = None  # iOS
    automation_name: str = ""  # auto-resolved
    appium_server: str = "http://localhost:4723"
    no_reset: bool = False
    full_reset: bool = False
    new_command_timeout: int = 300
    implicit_wait: int = 10
    screenshots_dir: str = "/tmp/appium_artifacts/screenshots"
    extra_caps: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.automation_name:
            self.automation_name = "XCUITest" if self.platform == Platform.IOS else "UiAutomator2"

    def to_capabilities(self) -> Dict[str, Any]:
        prefix = "appium"
        caps = {
            "platformName": "iOS" if self.platform == Platform.IOS else "Android",
            f"{prefix}:deviceName": self.device_name,
            f"{prefix}:platformVersion": self.platform_version,
            f"{prefix}:automationName": self.automation_name,
            f"{prefix}:noReset": self.no_reset,
            f"{prefix}:fullReset": self.full_reset,
            f"{prefix}:newCommandTimeout": self.new_command_timeout,
        }
        if self.app_path:
            caps[f"{prefix}:app"] = self.app_path
        if self.app_package:
            caps[f"{prefix}:appPackage"] = self.app_package
        if self.app_activity:
            caps[f"{prefix}:appActivity"] = self.app_activity
        if self.bundle_id:
            caps[f"{prefix}:bundleId"] = self.bundle_id
        caps.update(self.extra_caps)
        return caps


@dataclass
class NativeActionResult:
    action: str
    success: bool = True
    duration_ms: float = 0.0
    selector: Optional[str] = None
    screenshot_path: Optional[str] = None
    error: Optional[str] = None
    extras: Dict[str, Any] = field(default_factory=dict)


@dataclass
class NativeTestResult:
    platform: Platform
    device: str
    app: str
    passed: bool = True
    actions: List[NativeActionResult] = field(default_factory=list)
    duration_ms: float = 0.0
    logs: List[str] = field(default_factory=list)


class AppiumNative:
    """
    Appium 2.0 native mobile test driver.
    Wraps the appium-python-client with async support + extended capabilities.
    """

    def __init__(self, config: Optional[AppiumConfig] = None):
        self.config = config or AppiumConfig()
        self._driver = None

    # ── lifecycle ──────────────────────────────────────────────

    async def launch(self) -> "AppiumNative":
        from appium import webdriver as appium_webdriver
        from appium.options.common import AppiumOptions

        opts = AppiumOptions()
        caps = self.config.to_capabilities()
        for k, v in caps.items():
            opts.set_capability(k, v)

        loop = asyncio.get_event_loop()
        self._driver = await loop.run_in_executor(
            None,
            lambda: appium_webdriver.Remote(
                command_executor=self.config.appium_server,
                options=opts,
            ),
        )
        self._driver.implicitly_wait(self.config.implicit_wait)
        os.makedirs(self.config.screenshots_dir, exist_ok=True)

        logger.info("Appium session started: %s on %s (%s)",
                     self.config.app_path or self.config.bundle_id or self.config.app_package,
                     self.config.device_name, self.config.platform.value)
        return self

    async def quit(self) -> None:
        if self._driver:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._driver.quit)
            logger.info("Appium session ended.")

    # ── element interactions ───────────────────────────────────

    async def find_and_tap(self, strategy: str, locator: str) -> NativeActionResult:
        return await self._action("tap", strategy, locator)

    async def find_and_send_keys(self, strategy: str, locator: str, text: str) -> NativeActionResult:
        return await self._action("send_keys", strategy, locator, text=text)

    async def find_and_clear(self, strategy: str, locator: str) -> NativeActionResult:
        return await self._action("clear", strategy, locator)

    async def is_element_present(self, strategy: str, locator: str) -> bool:
        loop = asyncio.get_event_loop()
        try:
            elements = await loop.run_in_executor(
                None, lambda: self._driver.find_elements(strategy, locator)
            )
            return len(elements) > 0
        except Exception:
            return False

    async def get_element_text(self, strategy: str, locator: str) -> Optional[str]:
        loop = asyncio.get_event_loop()
        try:
            el = await loop.run_in_executor(
                None, lambda: self._driver.find_element(strategy, locator)
            )
            return await loop.run_in_executor(None, lambda: el.text)
        except Exception:
            return None

    async def get_element_attribute(self, strategy: str, locator: str, attr: str) -> Optional[str]:
        loop = asyncio.get_event_loop()
        try:
            el = await loop.run_in_executor(
                None, lambda: self._driver.find_element(strategy, locator)
            )
            return await loop.run_in_executor(None, lambda: el.get_attribute(attr))
        except Exception:
            return None

    # ── gestures ───────────────────────────────────────────────

    async def swipe(
        self,
        start: Tuple[int, int],
        end: Tuple[int, int],
        duration_ms: int = 500,
    ) -> NativeActionResult:
        import time
        t = time.monotonic()
        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(
                None,
                lambda: self._driver.swipe(
                    start[0], start[1], end[0], end[1], duration_ms,
                ),
            )
            return NativeActionResult(
                action="swipe", duration_ms=(time.monotonic() - t) * 1000,
                extras={"start": start, "end": end},
            )
        except Exception as e:
            return NativeActionResult(
                action="swipe", success=False,
                duration_ms=(time.monotonic() - t) * 1000, error=str(e),
            )

    async def scroll_down(self) -> NativeActionResult:
        size = self._driver.get_window_size()
        sx, sy = size["width"] // 2, int(size["height"] * 0.7)
        ex, ey = size["width"] // 2, int(size["height"] * 0.3)
        return await self.swipe((sx, sy), (ex, ey))

    async def scroll_up(self) -> NativeActionResult:
        size = self._driver.get_window_size()
        sx, sy = size["width"] // 2, int(size["height"] * 0.3)
        ex, ey = size["width"] // 2, int(size["height"] * 0.7)
        return await self.swipe((sx, sy), (ex, ey))

    async def long_press(self, strategy: str, locator: str, duration_ms: int = 1500) -> NativeActionResult:
        import time
        t = time.monotonic()
        loop = asyncio.get_event_loop()
        try:
            el = await loop.run_in_executor(
                None, lambda: self._driver.find_element(strategy, locator)
            )
            from appium.webdriver.common.touch_action import TouchAction
            action = TouchAction(self._driver)
            await loop.run_in_executor(
                None,
                lambda: action.long_press(el, duration=duration_ms).release().perform(),
            )
            return NativeActionResult(
                action="long_press", selector=locator,
                duration_ms=(time.monotonic() - t) * 1000,
            )
        except Exception as e:
            return NativeActionResult(
                action="long_press", success=False, selector=locator,
                duration_ms=(time.monotonic() - t) * 1000, error=str(e),
            )

    # ── push notifications ─────────────────────────────────────

    async def simulate_push_notification(
        self, title: str, body: str, data: Optional[Dict[str, str]] = None,
    ) -> NativeActionResult:
        """Simulate push notification via Appium."""
        loop = asyncio.get_event_loop()
        import time
        t = time.monotonic()
        try:
            if self.config.platform == Platform.IOS:
                payload = {
                    "aps": {
                        "alert": {"title": title, "body": body},
                        "sound": "default",
                    },
                }
                if data:
                    payload.update(data)
                await loop.run_in_executor(
                    None,
                    lambda: self._driver.execute_script(
                        "mobile: pushNotification",
                        {"payload": payload, "bundleId": self.config.bundle_id},
                    ),
                )
            else:
                # Android: use adb shell
                payload_str = json.dumps({"title": title, "body": body, **(data or {})})
                await loop.run_in_executor(
                    None,
                    lambda: self._driver.execute_script(
                        "mobile: shell",
                        {"command": "am", "args": [
                            "broadcast", "-a", "com.appium.PUSH_NOTIFICATION",
                            "--es", "payload", payload_str,
                        ]},
                    ),
                )
            return NativeActionResult(
                action="push_notification",
                duration_ms=(time.monotonic() - t) * 1000,
                extras={"title": title, "body": body},
            )
        except Exception as e:
            return NativeActionResult(
                action="push_notification", success=False,
                duration_ms=(time.monotonic() - t) * 1000, error=str(e),
            )

    # ── biometric auth ─────────────────────────────────────────

    async def enroll_biometric(self, bio_type: BiometricType = BiometricType.FINGERPRINT) -> NativeActionResult:
        loop = asyncio.get_event_loop()
        import time
        t = time.monotonic()
        try:
            if self.config.platform == Platform.IOS:
                await loop.run_in_executor(
                    None,
                    lambda: self._driver.execute_script(
                        "mobile: enrollBiometric", {"isEnabled": True},
                    ),
                )
            else:
                await loop.run_in_executor(
                    None,
                    lambda: self._driver.fingerprint(1),
                )
            return NativeActionResult(
                action="enroll_biometric",
                duration_ms=(time.monotonic() - t) * 1000,
                extras={"type": bio_type.value},
            )
        except Exception as e:
            return NativeActionResult(
                action="enroll_biometric", success=False,
                duration_ms=(time.monotonic() - t) * 1000, error=str(e),
            )

    async def send_biometric_match(self, match: bool = True) -> NativeActionResult:
        loop = asyncio.get_event_loop()
        import time
        t = time.monotonic()
        try:
            if self.config.platform == Platform.IOS:
                await loop.run_in_executor(
                    None,
                    lambda: self._driver.execute_script(
                        "mobile: sendBiometricMatch", {"type": "faceId", "match": match},
                    ),
                )
            else:
                if match:
                    await loop.run_in_executor(None, lambda: self._driver.fingerprint(1))
                else:
                    await loop.run_in_executor(None, lambda: self._driver.fingerprint(0))
            return NativeActionResult(
                action="biometric_match",
                duration_ms=(time.monotonic() - t) * 1000,
                extras={"match": match},
            )
        except Exception as e:
            return NativeActionResult(
                action="biometric_match", success=False,
                duration_ms=(time.monotonic() - t) * 1000, error=str(e),
            )

    # ── camera injection ───────────────────────────────────────

    async def inject_camera_image(self, image_path: str) -> NativeActionResult:
        """Push image to device camera roll / inject into camera preview."""
        loop = asyncio.get_event_loop()
        import time
        t = time.monotonic()
        try:
            with open(image_path, "rb") as f:
                img_data = base64.b64encode(f.read()).decode()

            if self.config.platform == Platform.IOS:
                await loop.run_in_executor(
                    None,
                    lambda: self._driver.push_file(
                        "@com.apple.DeveloperDiskImage/DCIM/camera_inject.jpg",
                        img_data,
                    ),
                )
            else:
                remote_path = "/sdcard/DCIM/Camera/camera_inject.jpg"
                await loop.run_in_executor(
                    None, lambda: self._driver.push_file(remote_path, img_data),
                )
                await loop.run_in_executor(
                    None,
                    lambda: self._driver.execute_script(
                        "mobile: shell",
                        {"command": "am", "args": [
                            "broadcast", "-a",
                            "android.intent.action.MEDIA_SCANNER_SCAN_FILE",
                            "-d", f"file://{remote_path}",
                        ]},
                    ),
                )
            return NativeActionResult(
                action="inject_camera",
                duration_ms=(time.monotonic() - t) * 1000,
                extras={"image": image_path},
            )
        except Exception as e:
            return NativeActionResult(
                action="inject_camera", success=False,
                duration_ms=(time.monotonic() - t) * 1000, error=str(e),
            )

    # ── network / offline mode ─────────────────────────────────

    async def set_network_condition(self, condition: NetworkCondition) -> NativeActionResult:
        loop = asyncio.get_event_loop()
        import time
        t = time.monotonic()
        try:
            connection_map = {
                NetworkCondition.WIFI: 2,
                NetworkCondition.CELLULAR: 4,
                NetworkCondition.AIRPLANE: 1,
                NetworkCondition.OFFLINE: 0,
                NetworkCondition.FULL: 6,
            }
            await loop.run_in_executor(
                None,
                lambda: self._driver.set_network_connection(connection_map[condition]),
            )
            return NativeActionResult(
                action="set_network",
                duration_ms=(time.monotonic() - t) * 1000,
                extras={"condition": condition.value},
            )
        except Exception as e:
            return NativeActionResult(
                action="set_network", success=False,
                duration_ms=(time.monotonic() - t) * 1000, error=str(e),
            )

    # ── deep links ─────────────────────────────────────────────

    async def open_deep_link(self, url: str) -> NativeActionResult:
        loop = asyncio.get_event_loop()
        import time
        t = time.monotonic()
        try:
            if self.config.platform == Platform.IOS:
                await loop.run_in_executor(
                    None,
                    lambda: self._driver.execute_script(
                        "mobile: deepLink",
                        {"url": url, "bundleId": self.config.bundle_id},
                    ),
                )
            else:
                await loop.run_in_executor(
                    None,
                    lambda: self._driver.execute_script(
                        "mobile: deepLink", {"url": url, "package": self.config.app_package},
                    ),
                )
            return NativeActionResult(
                action="deep_link",
                duration_ms=(time.monotonic() - t) * 1000,
                extras={"url": url},
            )
        except Exception as e:
            return NativeActionResult(
                action="deep_link", success=False,
                duration_ms=(time.monotonic() - t) * 1000, error=str(e),
            )

    # ── app lifecycle ──────────────────────────────────────────

    async def background_app(self, seconds: int = 5) -> NativeActionResult:
        loop = asyncio.get_event_loop()
        import time
        t = time.monotonic()
        try:
            await loop.run_in_executor(
                None, lambda: self._driver.background_app(seconds),
            )
            return NativeActionResult(
                action="background_app",
                duration_ms=(time.monotonic() - t) * 1000,
                extras={"seconds": seconds},
            )
        except Exception as e:
            return NativeActionResult(
                action="background_app", success=False,
                duration_ms=(time.monotonic() - t) * 1000, error=str(e),
            )

    async def terminate_app(self) -> NativeActionResult:
        loop = asyncio.get_event_loop()
        import time
        t = time.monotonic()
        try:
            app_id = self.config.bundle_id or self.config.app_package
            await loop.run_in_executor(
                None, lambda: self._driver.terminate_app(app_id),
            )
            return NativeActionResult(action="terminate_app", duration_ms=(time.monotonic() - t) * 1000)
        except Exception as e:
            return NativeActionResult(
                action="terminate_app", success=False,
                duration_ms=(time.monotonic() - t) * 1000, error=str(e),
            )

    async def activate_app(self) -> NativeActionResult:
        loop = asyncio.get_event_loop()
        import time
        t = time.monotonic()
        try:
            app_id = self.config.bundle_id or self.config.app_package
            await loop.run_in_executor(
                None, lambda: self._driver.activate_app(app_id),
            )
            return NativeActionResult(action="activate_app", duration_ms=(time.monotonic() - t) * 1000)
        except Exception as e:
            return NativeActionResult(
                action="activate_app", success=False,
                duration_ms=(time.monotonic() - t) * 1000, error=str(e),
            )

    # ── screenshots & logs ─────────────────────────────────────

    async def screenshot(self, name: str = "screenshot") -> str:
        path = os.path.join(self.config.screenshots_dir, f"{name}.png")
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None, lambda: self._driver.save_screenshot(path),
        )
        return path

    async def get_device_logs(self, log_type: str = "logcat") -> List[str]:
        loop = asyncio.get_event_loop()
        try:
            logs = await loop.run_in_executor(
                None, lambda: self._driver.get_log(log_type),
            )
            return [entry.get("message", "") for entry in logs]
        except Exception:
            return []

    # ── internal ───────────────────────────────────────────────

    async def _action(
        self, action: str, strategy: str, locator: str, **kwargs
    ) -> NativeActionResult:
        import time
        t = time.monotonic()
        loop = asyncio.get_event_loop()
        try:
            el = await loop.run_in_executor(
                None, lambda: self._driver.find_element(strategy, locator),
            )
            if action == "tap":
                await loop.run_in_executor(None, el.click)
            elif action == "send_keys":
                await loop.run_in_executor(None, lambda: el.send_keys(kwargs["text"]))
            elif action == "clear":
                await loop.run_in_executor(None, el.clear)
            return NativeActionResult(
                action=action, selector=locator,
                duration_ms=(time.monotonic() - t) * 1000,
            )
        except Exception as e:
            return NativeActionResult(
                action=action, success=False, selector=locator,
                duration_ms=(time.monotonic() - t) * 1000, error=str(e),
            )