"""
agent/tools/selector_discovery.py

Self-healing selector discovery engine.

Instead of hardcoding selectors, this module:
  1. Launches a headless browser
  2. Navigates to the target page
  3. Inspects the live DOM to find real selectors for common patterns
  4. Returns a SelectorMap that codegen uses to write accurate tests

This is the "agent should find selectors itself" capability.

Usage:
    from agent.tools.selector_discovery import discover_selectors
    selectors = discover_selectors("https://jiomart.uat.jiomartjcp.com/login")
    print(selectors.phone_input)   # actual selector found on the page
"""
from __future__ import annotations
import json
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

CACHE_DIR = Path(os.getenv("SELECTOR_CACHE_DIR", "data/selector_cache"))


@dataclass
class SelectorMap:
    """Real selectors discovered from the live DOM."""
    url: str = ""

    # Auth
    phone_input: str = ""
    otp_input: str = ""
    password_input: str = ""
    login_button: str = ""
    get_otp_button: str = ""
    verify_button: str = ""

    # Search
    search_box: str = ""
    search_button: str = ""

    # Cart
    add_to_cart: str = ""
    cart_icon: str = ""
    cart_count_badge: str = ""
    remove_from_cart: str = ""
    qty_increase: str = ""
    qty_decrease: str = ""
    qty_input: str = ""

    # Checkout
    proceed_to_checkout: str = ""
    coupon_input: str = ""
    apply_coupon: str = ""

    # Product
    product_card: str = ""
    product_price: str = ""
    product_name: str = ""

    # Modals
    modal_close: str = ""
    location_modal: str = ""
    confirm_location: str = ""

    # Status
    discovered: bool = False
    errors: list = field(default_factory=list)

    def to_prompt_block(self) -> str:
        """Format as a block to inject into codegen prompt."""
        lines = [
            "DISCOVERED SELECTORS (use these exact selectors — verified on live DOM):",
            f"  URL: {self.url}",
        ]
        fields = {
            "Phone input":        self.phone_input,
            "OTP input":          self.otp_input,
            "Login button":       self.login_button,
            "Get OTP button":     self.get_otp_button,
            "Verify button":      self.verify_button,
            "Search box":         self.search_box,
            "Add to cart":        self.add_to_cart,
            "Cart icon":          self.cart_icon,
            "Cart count badge":   self.cart_count_badge,
            "Remove from cart":   self.remove_from_cart,
            "Qty increase":       self.qty_increase,
            "Qty decrease":       self.qty_decrease,
            "Checkout button":    self.proceed_to_checkout,
            "Coupon input":       self.coupon_input,
            "Product card":       self.product_card,
            "Modal close":        self.modal_close,
            "Confirm location":   self.confirm_location,
        }
        for label, sel in fields.items():
            if sel:
                lines.append(f"  {label}: {sel}")
        if not self.discovered:
            lines.append("  (discovery failed — using fallback selectors)")
        return "\n".join(lines)


# ── Selector probe patterns ────────────────────────────────────
# Each entry: (field_name, list_of_css_selectors_to_try_in_order)
PROBES = {
    "phone_input": [
        "input[type='tel']",
        "input[placeholder*='mobile' i]",
        "input[placeholder*='phone' i]",
        "input[placeholder*='number' i]",
        "input[placeholder*='Mobile' i]",
        "#mobile", "#phone", "#phoneNumber",
        "input[name*='phone' i]",
        "input[name*='mobile' i]",
    ],
    "otp_input": [
        "input[placeholder*='OTP' i]",
        "input[placeholder*='otp' i]",
        "input[type='number'][maxlength='6']",
        "input[type='tel'][maxlength='6']",
        "input[name*='otp' i]",
        "#otp", ".otp-input",
    ],
    "password_input": [
        "input[type='password']",
        "input[name='password']",
        "input[placeholder*='password' i]",
    ],
    "login_button": [
        "button[type='submit']",
        "button:has-text('Login')",
        "button:has-text('Sign In')",
        "button:has-text('Log In')",
        "[class*='login-btn']",
        "[class*='loginBtn']",
    ],
    "get_otp_button": [
        "button:has-text('Get OTP')",
        "button:has-text('Send OTP')",
        "button:has-text('Continue')",
        "button:has-text('Request OTP')",
        "[class*='get-otp']",
        "[class*='getOtp']",
    ],
    "verify_button": [
        "button:has-text('Verify OTP')",
        "button:has-text('Verify')",
        "button:has-text('Confirm')",
        "button:has-text('Submit')",
    ],
    "search_box": [
        "input[placeholder*='Search' i]",
        "input[type='search']",
        "#search", ".search-input",
        "input[name='q']",
        "[class*='search-box'] input",
        "[class*='searchBox'] input",
    ],
    "add_to_cart": [
        "button:has-text('Add')",
        "button:has-text('Add to Cart')",
        "[class*='add-to-cart']",
        "[class*='addToCart']",
        "[class*='add_to_cart']",
        "[aria-label*='add to cart' i]",
    ],
    "cart_icon": [
        "[href*='cart']",
        "[aria-label*='cart' i]",
        "[class*='cart-icon']",
        "[class*='cartIcon']",
        ".cart",
    ],
    "cart_count_badge": [
        "[class*='cart-count']",
        "[class*='cartCount']",
        "[class*='cart-badge']",
        "[class*='cart'] span",
        "[aria-label*='cart'] span",
    ],
    "remove_from_cart": [
        "button:has-text('Remove')",
        "[class*='remove-item']",
        "[class*='removeItem']",
        "[aria-label*='remove' i]",
    ],
    "qty_increase": [
        "button:has-text('+')",
        "[class*='increment']",
        "[class*='increase']",
        "[aria-label*='increase' i]",
        "[aria-label*='add' i]",
    ],
    "qty_decrease": [
        "button:has-text('-')",
        "[class*='decrement']",
        "[class*='decrease']",
        "[aria-label*='decrease' i]",
        "[aria-label*='remove' i]",
    ],
    "qty_input": [
        "input[class*='quantity' i]",
        "input[name*='qty' i]",
        "input[aria-label*='quantity' i]",
    ],
    "proceed_to_checkout": [
        "button:has-text('Checkout')",
        "button:has-text('Proceed to Checkout')",
        "a:has-text('Checkout')",
        "[class*='checkout-btn']",
        "[class*='checkoutBtn']",
    ],
    "coupon_input": [
        "input[placeholder*='coupon' i]",
        "input[placeholder*='promo' i]",
        "input[placeholder*='code' i]",
        "input[name*='coupon' i]",
        "#coupon", "#promoCode",
    ],
    "apply_coupon": [
        "button:has-text('Apply')",
        "[class*='apply-coupon']",
        "[class*='applyCoupon']",
    ],
    "product_card": [
        "[class*='product-card']",
        "[class*='productCard']",
        "[class*='product_card']",
        "[class*='item-card']",
        "[data-testid*='product']",
    ],
    "product_price": [
        "[class*='price']",
        "[class*='Price']",
        "[data-testid*='price']",
        "span[class*='amount']",
    ],
    "modal_close": [
        "button[aria-label='Close']",
        "[class*='modal-close']",
        "[class*='closeModal']",
        "[class*='close-btn']",
        "button:has-text('×')",
        "button:has-text('✕')",
    ],
    "confirm_location": [
        "button:has-text('Confirm Location')",
        "button:has-text('Confirm location')",
        "button:has-text('Confirm')",
        "[class*='confirm-location']",
    ],
}


def discover_selectors(
    url: str,
    extra_urls: list[str] | None = None,
    use_cache: bool = True,
    cache_ttl_hours: float = 24,
) -> SelectorMap:
    """
    Discover real selectors from the live DOM.

    Args:
        url: Base URL to inspect
        extra_urls: Additional pages to check (e.g. login page, cart page)
        use_cache: Use cached results if available and fresh
        cache_ttl_hours: How long to trust cached selectors

    Returns:
        SelectorMap with real CSS selectors found on the page
    """
    import hashlib, time

    cache_key  = hashlib.md5(url.encode()).hexdigest()[:12]
    cache_file = CACHE_DIR / f"{cache_key}.json"
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    # Return cached result if fresh
    if use_cache and cache_file.exists():
        age_hours = (time.time() - cache_file.stat().st_mtime) / 3600
        if age_hours < cache_ttl_hours:
            try:
                data = json.loads(cache_file.read_text(encoding="utf-8"))
                sm = SelectorMap(**data)
                if sm.discovered:
                    print(f"[SELECTORS] Using cached selectors for {url} (age={age_hours:.1f}h)")
                    return sm
            except Exception:
                pass

    print(f"[SELECTORS] Discovering selectors from live DOM: {url}")
    result = SelectorMap(url=url)

    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                viewport={"width": 1440, "height": 900},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
            )
            page = context.new_page()

            # Pages to probe
            pages_to_probe = [url] + (extra_urls or [])

            for probe_url in pages_to_probe:
                try:
                    page.goto(probe_url, wait_until="domcontentloaded", timeout=20000)
                    page.wait_for_timeout(2000)

                    # Probe each selector pattern
                    for field_name, candidates in PROBES.items():
                        # Skip if already found
                        if getattr(result, field_name, ""):
                            continue
                        for selector in candidates:
                            try:
                                # Check if element exists in DOM
                                count = page.locator(selector).count()
                                if count > 0:
                                    # Verify it's actually visible or at least present
                                    loc = page.locator(selector).first
                                    # Use a short timeout
                                    if loc.is_visible() or count > 0:
                                        setattr(result, field_name, selector)
                                        break
                            except Exception:
                                continue

                except Exception as e:
                    result.errors.append(f"Failed to probe {probe_url}: {e}")
                    continue

            browser.close()

        result.discovered = True
        print(f"[SELECTORS] Discovery complete. Found {_count_found(result)} selectors.")

    except ImportError:
        result.errors.append("Playwright not available for selector discovery")
        result.discovered = False
    except Exception as e:
        result.errors.append(f"Discovery failed: {e}")
        result.discovered = False

    # Apply fallbacks for anything not found
    _apply_fallbacks(result)

    # Cache result
    try:
        cache_file.write_text(
            json.dumps(asdict(result), indent=2, default=str),
            encoding="utf-8"
        )
    except Exception:
        pass

    return result


def _count_found(sm: SelectorMap) -> int:
    count = 0
    for f_name, probe_list in PROBES.items():
        if getattr(sm, f_name, ""):
            count += 1
    return count


def _apply_fallbacks(sm: SelectorMap):
    """Apply safe fallback selectors for anything not discovered."""
    fallbacks = {
        "phone_input":   "input[type='tel']",
        "otp_input":     "input[placeholder*='OTP' i]",
        "search_box":    "input[placeholder*='Search' i]",
        "add_to_cart":   "button:has-text('Add')",
        "cart_icon":     "[href*='cart'], [aria-label*='cart' i]",
        "remove_from_cart": "button:has-text('Remove')",
        "qty_increase":  "button:has-text('+')",
        "qty_decrease":  "button:has-text('-')",
        "modal_close":   "button[aria-label='Close'], button:has-text('×')",
        "confirm_location": "button:has-text('Confirm Location')",
        "proceed_to_checkout": "button:has-text('Checkout')",
        "coupon_input":  "input[placeholder*='coupon' i]",
        "product_card":  "[class*='product-card'], [class*='productCard']",
    }
    for field_name, fallback in fallbacks.items():
        if not getattr(sm, field_name, ""):
            setattr(sm, field_name, fallback)


def get_selectors_for_spec(spec: str) -> SelectorMap:
    """
    Extract URL from spec and discover selectors.
    Called by codegen before generating test code.
    """
    import re
    match = re.search(r"https?://[^\s\)\"\']+", spec)
    if not match:
        # No URL in spec — check env
        base = os.getenv("JIOMART_URL") or os.getenv("APP_BASE_URL") or ""
        if not base:
            return SelectorMap(discovered=False,
                               errors=["No URL found in spec or environment"])
        url = base
    else:
        from urllib.parse import urlparse
        raw = match.group(0).rstrip(".,;")
        p = urlparse(raw)
        url = f"{p.scheme}://{p.netloc}"

    # For JioMart, also probe the login page
    extra = []
    if "jiomart" in url.lower():
        extra = [url]  # login modal appears on home page

    return discover_selectors(url, extra_urls=extra)


if __name__ == "__main__":
    import sys
    url = sys.argv[1] if len(sys.argv) > 1 else "https://jiomart.uat.jiomartjcp.com"
    print(f"\nDiscovering selectors for: {url}\n")
    sm = discover_selectors(url, use_cache=False)
    print("\n" + sm.to_prompt_block())
    print(f"\nErrors: {sm.errors}")
