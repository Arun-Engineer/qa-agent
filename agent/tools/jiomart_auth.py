"""
agent/tools/jiomart_auth.py

Handles the FULL JioMart UAT modal chain:

  Modal Chain on every page load:
  ┌─────────────────────────────────────────────┐
  │ 1. "Enable location Services"               │
  │    → click "Select Location Manually"       │
  ├─────────────────────────────────────────────┤
  │ 2. "Choose your delivery address"           │
  │    → type pincode in search box             │
  │    → click first result from dropdown       │
  ├─────────────────────────────────────────────┤
  │ 3. "Day's offer" promo popup (optional)     │
  │    → click X                                │
  └─────────────────────────────────────────────┘

  Then: OTP login → save session → tests run

Credentials in .env:
  JIOMART_URL=https://jiomart.uat.jiomartjcp.com
  JIOMART_PHONE=your_test_phone
  JIOMART_OTP=123456
  JIOMART_PINCODE=400001
"""
from __future__ import annotations
import os, json, time
from pathlib import Path
from playwright.sync_api import Page, TimeoutError as PWTimeout

# ── Config ────────────────────────────────────────────────────
BASE_URL  = os.getenv("JIOMART_URL",     "https://jiomart.uat.jiomartjcp.com")
PHONE     = os.getenv("JIOMART_PHONE",   "")
OTP       = os.getenv("JIOMART_OTP",     "123456")
PINCODE   = os.getenv("JIOMART_PINCODE", "400001")
AUTH_FILE = Path(os.getenv("JIOMART_AUTH_STATE", "data/jiomart_auth_state.json"))

S  = 2000   # short wait ms
M  = 5000   # medium wait ms


# ══════════════════════════════════════════════════════════════
# MODAL CHAIN HANDLER
# ══════════════════════════════════════════════════════════════

def handle_modal_chain(page: Page, max_rounds: int = 5):
    """
    Keeps dismissing modals until none are visible.
    Handles the full chain:
      Round 1 → "Enable location Services" 
      Round 2 → "Choose your delivery address"
      Round 3 → Promo/offer popup
      Round 4+ → Any remaining modals
    """
    for round_num in range(1, max_rounds + 1):
        dismissed = (
            _handle_location_modal(page)
            or _handle_delivery_address_modal(page)
            or _handle_promo_popup(page)
            or _handle_generic_modal(page)
        )
        if not dismissed:
            break   # No more modals
        page.wait_for_timeout(1000)
    print(f"[MODAL] Chain handled in {round_num} round(s)")


def _handle_location_modal(page: Page) -> bool:
    """
    Modal 1: 'Enable location Services'
    → clicks 'Select Location Manually' which opens delivery address modal
    """
    try:
        heading = page.locator("text=Enable location Services").first
        heading.wait_for(timeout=3000)
    except PWTimeout:
        return False

    print("[MODAL] Handling: Enable location Services")

    # Click 'Select Location Manually'
    try:
        page.get_by_text("Select Location Manually").first.click()
        page.wait_for_timeout(S)
        return True
    except Exception:
        pass

    # Fallback: click X
    for sel in ["button[aria-label='Close']", ".modal-close",
                "button:has-text('×')", "button:has-text('✕')"]:
        try:
            btn = page.locator(sel).first
            if btn.is_visible(timeout=1000):
                btn.click()
                page.wait_for_timeout(S)
                return True
        except Exception:
            pass

    page.keyboard.press("Escape")
    page.wait_for_timeout(1000)
    return True


def _handle_delivery_address_modal(page: Page) -> bool:
    """
    Modal 2: 'Choose your delivery address' — 3-screen flow:

    Screen 1: Search box
      → type pincode → Google autocomplete appears → click first result

    Screen 2: Map with pin + "Confirm Location" button
      → click "Confirm Location"

    Screen 3: Browser "wants to access local network" permission popup
      → click "Block" (we don't need local network access)
    """
    try:
        heading = page.locator("text=Choose your delivery address").first
        heading.wait_for(timeout=3000)
    except PWTimeout:
        return False

    print(f"[MODAL] Handling: Choose your delivery address (pincode={PINCODE})")

    # ── Screen 1: type pincode, click first Google autocomplete result ──
    search_selectors = [
        "input[placeholder*='area']",
        "input[placeholder*='landmark']",
        "input[placeholder*='street']",
        "input[placeholder*='name']",
        "input[placeholder*='Search']",
        "input[placeholder*='pincode']",
        "input[type='search']",
        "input[type='text']",
    ]
    search_input = None
    for sel in search_selectors:
        try:
            inp = page.locator(sel).first
            if inp.is_visible(timeout=2000):
                search_input = inp
                break
        except Exception:
            pass

    if not search_input:
        print("[MODAL] No search input found — pressing Escape")
        page.keyboard.press("Escape")
        page.wait_for_timeout(1000)
        return True

    # Type pincode slowly so Google autocomplete fires
    search_input.click()
    search_input.fill("")
    page.wait_for_timeout(300)
    search_input.type(PINCODE, delay=100)
    page.wait_for_timeout(2000)  # Wait for Google Places autocomplete

    # Click first autocomplete result (Google Places uses .pac-item)
    first_result_selectors = [
        ".pac-item:first-child",           # Google Places (Image 1)
        ".pac-item",                        # Any Google Places item
        "[class*='suggestion']:first-child",
        "[class*='autocomplete'] li:first-child",
        "[class*='dropdown-item']:first-child",
        "ul li:first-child",
    ]
    clicked = False
    for sel in first_result_selectors:
        try:
            result = page.locator(sel).first
            if result.is_visible(timeout=2000):
                result.click()
                page.wait_for_timeout(2000)
                clicked = True
                print(f"[MODAL] Clicked autocomplete result: {sel}")
                break
        except Exception:
            pass

    if not clicked:
        # Fallback: arrow down + enter
        search_input.press("ArrowDown")
        page.wait_for_timeout(500)
        search_input.press("Enter")
        page.wait_for_timeout(2000)

    # ── Screen 2: Map appears → click "Confirm Location" ──────────────
    try:
        confirm_btn = page.get_by_role("button", name="Confirm Location").first
        confirm_btn.wait_for(timeout=5000)
        confirm_btn.click()
        page.wait_for_timeout(2000)
        print("[MODAL] Clicked: Confirm Location")
    except PWTimeout:
        # Try other confirm variants
        for btn_text in ["Confirm location", "Confirm", "Apply", "Set Location", "Done"]:
            try:
                btn = page.get_by_role("button", name=btn_text).first
                if btn.is_visible(timeout=1500):
                    btn.click()
                    page.wait_for_timeout(2000)
                    print(f"[MODAL] Clicked: {btn_text}")
                    break
            except Exception:
                pass

    # ── Screen 3: Browser "wants to access local network" popup ────────
    # This is a browser-level dialog — Playwright handles it via dialog event
    # Also handle it if it appears as a page element
    try:
        block_btn = page.get_by_role("button", name="Block").first
        if block_btn.is_visible(timeout=3000):
            block_btn.click()
            page.wait_for_timeout(1000)
            print("[MODAL] Dismissed: local network permission popup (Block)")
    except Exception:
        pass

    # Dismiss any remaining overlay by pressing Escape
    page.keyboard.press("Escape")
    page.wait_for_timeout(500)

    return True


def _handle_promo_popup(page: Page) -> bool:
    """
    Modal 3: 'Day's offer' or any promo popup
    → closes it
    """
    promo_signals = [
        "text=Day's offer",
        "text=View My list",
        "[class*='offer'][class*='popup']",
        "[class*='promo'][class*='modal']",
    ]
    for signal in promo_signals:
        try:
            el = page.locator(signal).first
            el.wait_for(timeout=2000)
            print("[MODAL] Handling: Promo popup")
            # Find close button
            for close_sel in [
                "button[aria-label='Close']",
                "[class*='close']",
                "button:has-text('×')",
                "button:has-text('✕')",
            ]:
                try:
                    btn = page.locator(close_sel).first
                    if btn.is_visible(timeout=1000):
                        btn.click()
                        page.wait_for_timeout(S)
                        return True
                except Exception:
                    pass
            page.keyboard.press("Escape")
            page.wait_for_timeout(1000)
            return True
        except PWTimeout:
            continue
    return False


def _handle_generic_modal(page: Page) -> bool:
    """Last resort: close any visible modal overlay."""
    try:
        # Look for a modal backdrop
        overlay = page.locator(
            "[class*='modal']:visible, [class*='overlay']:visible, [class*='dialog']:visible"
        ).first
        overlay.wait_for(timeout=1500)
        print("[MODAL] Handling: Generic modal overlay")
        page.keyboard.press("Escape")
        page.wait_for_timeout(1000)
        return True
    except Exception:
        return False


# ══════════════════════════════════════════════════════════════
# AUTH FLOW
# ══════════════════════════════════════════════════════════════

def is_logged_in(page: Page) -> bool:
    for sel in ["[class*='user-name']", "[class*='account-name']",
                "text=My Account", "[aria-label*='account'] span"]:
        try:
            if page.locator(sel).first.is_visible(timeout=1500):
                return True
        except Exception:
            pass
    return False


def login_with_otp(page: Page, phone: str = PHONE, otp: str = OTP) -> bool:
    if not phone:
        raise ValueError("JIOMART_PHONE not set in .env")

    print(f"[AUTH] Logging in as {phone}")

    # Click account/login icon in header
    for sel in ["[class*='login']", "[aria-label*='login']",
                "[aria-label*='Login']", "text=Login", ".user-icon",
                "[class*='account'] button", "button[class*='user']"]:
        try:
            btn = page.locator(sel).first
            if btn.is_visible(timeout=2000):
                btn.click()
                page.wait_for_timeout(S)
                break
        except Exception:
            pass

    # Dismiss any modals that appear after clicking login
    handle_modal_chain(page)

    # Enter phone
    for sel in ["input[type='tel']", "input[placeholder*='mobile']",
                "input[placeholder*='Mobile']", "input[placeholder*='phone']",
                "input[placeholder*='number']", "#mobile", "#phone"]:
        try:
            inp = page.locator(sel).first
            if inp.is_visible(timeout=3000):
                inp.click()
                inp.fill(phone)
                page.wait_for_timeout(500)
                break
        except Exception:
            pass

    # Get OTP button
    for text in ["Get OTP", "Continue", "Send OTP", "Request OTP", "Next"]:
        try:
            btn = page.get_by_role("button", name=text).first
            if btn.is_visible(timeout=2000):
                btn.click()
                page.wait_for_timeout(S)
                break
        except Exception:
            pass

    # Enter OTP — try single input first
    otp_entered = False
    for sel in ["input[placeholder*='OTP']", "input[placeholder*='otp']",
                "input[type='number'][maxlength='6']",
                "input[type='tel'][maxlength='6']", "#otp", ".otp-input"]:
        try:
            inp = page.locator(sel).first
            if inp.is_visible(timeout=5000):
                inp.click()
                inp.fill(otp)
                otp_entered = True
                break
        except Exception:
            pass

    # Try 6 individual digit boxes
    if not otp_entered:
        try:
            boxes = page.locator("input[maxlength='1']").all()
            if len(boxes) >= 6:
                for i, digit in enumerate(otp[:6]):
                    boxes[i].fill(digit)
                    page.wait_for_timeout(100)
                otp_entered = True
        except Exception:
            pass

    if not otp_entered:
        raise RuntimeError("Could not find OTP input")

    # Submit
    for text in ["Verify OTP", "Verify", "Submit", "Login", "Confirm"]:
        try:
            btn = page.get_by_role("button", name=text).first
            if btn.is_visible(timeout=2000):
                btn.click()
                page.wait_for_timeout(S * 2)
                break
        except Exception:
            pass

    page.wait_for_timeout(2000)
    return is_logged_in(page)


def save_auth_state(page: Page):
    AUTH_FILE.parent.mkdir(parents=True, exist_ok=True)
    state = page.context.storage_state()
    AUTH_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    print(f"[AUTH] Session saved → {AUTH_FILE}")


def auth_state_valid() -> bool:
    if not AUTH_FILE.exists():
        return False
    return (time.time() - AUTH_FILE.stat().st_mtime) / 3600 < 4


# ══════════════════════════════════════════════════════════════
# MAIN SETUP — call this at start of every test
# ══════════════════════════════════════════════════════════════

def setup_jiomart_page(page: Page, force_login: bool = False) -> Page:
    """
    Full JioMart page setup. Call at start of every test:

        def test_add_to_cart(page: Page):
            page = setup_jiomart_page(page)
            # page is authenticated, all modals dismissed

    Flow:
      1. Navigate to BASE_URL
      2. Handle full modal chain (location → delivery address → promos)
      3. Login with OTP if not already authenticated
      4. Save session for reuse (valid 4 hours)
      5. Return ready-to-use page
    """
    page.set_default_timeout(15000)

    # Deny browser permission popups proactively (local network, notifications etc)
    # This prevents the 'wants to access local network' popup in Image 3
    try:
        page.context.grant_permissions([])
    except Exception:
        pass

    # Navigate
    page.goto(BASE_URL)
    page.wait_for_load_state("networkidle", timeout=15000)

    # Handle ALL modals in sequence
    handle_modal_chain(page)

    # Auth
    if not force_login and is_logged_in(page):
        print("[AUTH] Already logged in")
        return page

    if PHONE:
        try:
            if login_with_otp(page):
                print("[AUTH] Login successful")
                save_auth_state(page)
                page.goto(BASE_URL)
                page.wait_for_load_state("networkidle")
                handle_modal_chain(page)
            else:
                print("[AUTH] Login attempted — could not verify")
        except Exception as e:
            print(f"[AUTH] Login error: {e}")
    else:
        print("[AUTH] JIOMART_PHONE not set — skipping login")

    return page
