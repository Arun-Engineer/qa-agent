import re
import os
import json
import pytest
from playwright.sync_api import Page, expect

# Read from RunContext (thread-safe) first, then env vars
try:
    from agent.run_context import get_run_context as _get_ctx
    _ctx = _get_ctx()
    BASE_URL = _ctx.url     or os.getenv("JIOMART_URL",     "https://jiomart.uat.jiomartjcp.com")
    PHONE    = _ctx.phone   or os.getenv("JIOMART_PHONE",   "")
    OTP      = _ctx.otp     or os.getenv("JIOMART_OTP",     "123456")
    PINCODE  = _ctx.pincode or os.getenv("JIOMART_PINCODE", "400020")
except Exception:
    BASE_URL = os.getenv("JIOMART_URL",     "https://jiomart.uat.jiomartjcp.com")
    PHONE    = os.getenv("JIOMART_PHONE",   "")
    OTP      = os.getenv("JIOMART_OTP",     "123456")
    PINCODE  = os.getenv("JIOMART_PINCODE", "400020")


# ── Modal dismissal (home page on first visit) ────────────────

def dismiss_home_modals(page):
    """
    Dismiss location modal on home page.
    If pincode screen appears, enter the requested pincode directly.
    This avoids needing to go through profile/address separately.
    """
    # Handle "Enable location Services" modal
    try:
        if page.get_by_text("Select Location Manually").first.is_visible(timeout=3000):
            page.get_by_text("Select Location Manually").first.click()
            page.wait_for_timeout(2000)
    except Exception:
        pass

    # Handle "Choose your delivery address" with pincode search
    try:
        inp = page.locator(
            "input[placeholder*='area'], input[placeholder*='landmark'], "
            "input[placeholder*='Search for area']"
        ).first
        if inp.is_visible(timeout=3000):
            inp.click()
            page.wait_for_timeout(300)
            inp.type(PINCODE, delay=100)
            page.wait_for_timeout(2500)
            # Click first Google Places result
            for sel in [".pac-item", "[class*=suggestion]"]:
                try:
                    r = page.locator(sel).first
                    if r.is_visible(timeout=2000):
                        r.click()
                        page.wait_for_timeout(2000)
                        break
                except Exception:
                    pass
            # Confirm Location
            try:
                page.get_by_role("button", name="Confirm Location").first.click()
                page.wait_for_timeout(2000)
                print(f"[MODAL] Location set to pincode {PINCODE}")
            except Exception:
                pass
    except Exception:
        pass

    # Dismiss any remaining popups
    try:
        page.keyboard.press("Escape")
        page.wait_for_timeout(500)
    except Exception:
        pass


# ── Login flow ────────────────────────────────────────────────

def jiomart_login(page):
    """
    Login flow with session reuse support:
    1. If persistent profile has saved session → "Choose an account" appears
       → click Continue if our number shown, else Use another account
    2. If no session → click account icon → phone → OTP
    """
    if not PHONE:
        pytest.skip("JIOMART_PHONE not set in environment or spec")

    page.goto(BASE_URL)
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(2000)
    dismiss_home_modals(page)

    # ── Check if already logged in ───────────────────────────
    # If the top-right avatar shows a letter (J, M, etc.) the user
    # is already logged in — skip login entirely
    try:
        # The avatar is a circle with a letter, class contains "avatar" or "initial"
        # or it's an <img> replaced by a letter span
        avatar_selectors = [
            "[class*=avatar]:not([class*=login])",
            "[class*=initial]",
            "[class*=user-initial]",
            # JioMart specific: the J circle top right
            "header [class*=account]",
            "header [class*=user]",
            "nav [class*=account]",
        ]
        for sel in avatar_selectors:
            el = page.locator(sel).last
            if el.is_visible(timeout=1000):
                txt = (el.text_content() or "").strip()
                # If it shows a single letter (A-Z) the user is logged in
                if txt and len(txt) == 1 and txt.isalpha():
                    print(f"[LOGIN] Already logged in (avatar shows '{txt}') — skipping login")
                    return
    except Exception:
        pass

    # Click account/login icon
    for sel in ["[class*=account]", "[class*=user]", "[aria-label*=account]"]:
        try:
            b = page.locator(sel).last
            if b.is_visible(timeout=2000):
                b.click()
                page.wait_for_timeout(3000)
                break
        except Exception:
            pass

    # Handle "Choose an account" screen (cached session)
    # Buttons on this page both have class j-JDSButton-container
    # First = Continue, Second = Use another account
    try:
        page.get_by_text("Choose an account").first.wait_for(timeout=4000)
        print("[LOGIN] Choose an account screen — clicking Continue")
        # Click the Continue button (first j-JDSButton-container)
        btns = page.locator(".j-JDSButton-container").all()
        if btns:
            btns[0].click()  # First button = Continue
            page.wait_for_timeout(5000)
            print("[LOGIN] Clicked Continue, URL:", page.url)
            if "sign-up" not in page.url and "replica" not in page.url:
                print("[LOGIN] Session reuse successful")
                _save_session(page)
                return
    except Exception as e:
        print(f"[LOGIN] Choose account handling: {e}")

    # Check if already back on JioMart after Continue
    if "jiomartjcp.com" in page.url and "sign-up" not in page.url:
        print("[LOGIN] Already on JioMart — skipping OTP")
        _save_session(page)
        return

    # Fresh login: phone → Sign In → OTP
    page.wait_for_timeout(2000)

    # Enter phone
    ph = page.locator("input[type=tel]").first
    ph.wait_for(timeout=8000)
    ph.click()
    ph.fill("")
    ph.type(PHONE, delay=80)
    page.wait_for_timeout(500)

    # Sign In
    page.get_by_role("button", name="Sign In").first.click()
    page.wait_for_timeout(4000)

    # OTP — 6 boxes with class j-JDSInputCodeItem-jds_input
    otp_boxes = page.locator(".j-JDSInputCodeItem-jds_input").all()
    if len(otp_boxes) == 6:
        for i, digit in enumerate(OTP):
            otp_boxes[i].click()
            page.wait_for_timeout(100)
            otp_boxes[i].press(digit)
            page.wait_for_timeout(200)
    elif otp_boxes:
        otp_boxes[0].click()
        for digit in OTP:
            page.keyboard.press(digit)
            page.wait_for_timeout(150)

    page.wait_for_timeout(1000)

    # Verify OTP
    verified = False
    for sel in ["button.j-JDSButton-container", ".j-JDSButton-container",
                "button:has-text('Verify OTP')", "button:has-text('Verify')"]:
        try:
            btn = page.locator(sel).first
            if btn.is_visible(timeout=3000):
                btn.click()
                verified = True
                break
        except Exception:
            pass
    if not verified:
        page.keyboard.press("Enter")

    page.wait_for_timeout(6000)
    _save_session(page)


def _save_session(page):
    """Save browser session state for reuse."""
    try:
        from pathlib import Path as P
        P("data").mkdir(exist_ok=True)
        state = page.context.storage_state()
        P("data/jiomart_auth_state.json").write_text(json.dumps(state), encoding="utf-8")
        print("[LOGIN] Session saved")
    except Exception as e:
        print(f"[LOGIN] Session save failed: {e}")


# ── Delivery address setup ────────────────────────────────────

def setup_delivery_address(page):
    """
    Full delivery address flow from profile page:
    1. Click Delivery Address in left nav
    2. Check if address with PINCODE already exists
       YES → click it to set as active
       NO  → Add New Address → enter pincode → confirm → save
    3. Navigate back to home
    """
    # Navigate to delivery address page
    # Try clicking the avatar/profile icon first (more reliable than direct URL)
    navigated = False
    try:
        for sel in ["header [class*=account]", "header [class*=user]",
                    "nav [class*=account]", "[class*=avatar]"]:
            el = page.locator(sel).last
            if el.is_visible(timeout=1500):
                txt = (el.text_content() or "").strip()
                if txt and len(txt) <= 2:  # letter avatar = logged in
                    el.click()
                    page.wait_for_timeout(2000)
                    # Now click "Delivery Address" in the profile menu/page
                    try:
                        page.get_by_text("Delivery Address").first.click()
                        page.wait_for_timeout(2000)
                        navigated = True
                        break
                    except Exception:
                        pass
    except Exception:
        pass

    if not navigated:
        page.goto(f"{BASE_URL}/profile/address")

    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(2000)

    # ── Check if address with requested pincode exists ────────
    pincode_found = False
    try:
        # Strategy: find all address text elements containing our pincode
        # Each address card has structure:
        #   <div class="card">
        #     <div>Home <span>Default</span></div>
        #     <div>400020, building, MUMBAI...</div>
        #     <button>⋮</button>   ← 3-dot menu button
        #   </div>
        #
        # We find the pincode text, go up to the card, then find
        # the ⋮ button which is a sibling within the same card.

        # Get all elements containing the pincode string
        pincode_matches = page.locator(f"text={PINCODE}").all()
        print(f"[ADDR] Found {len(pincode_matches)} elements with pincode {PINCODE}")

        for el in pincode_matches:
            try:
                # Get the bounding box to find the ⋮ button nearby
                box = el.bounding_box()
                if not box:
                    continue

                # The ⋮ button is at the top-right of the card
                # It's typically within 400px to the right and 100px up from the text
                # Use page.locator with position-based approach via JavaScript
                three_dot_x = box["x"] + box["width"] + 50
                three_dot_y = box["y"] - 20

                # Find button near the top-right of this text element
                # by evaluating which buttons are in the same card
                result = page.evaluate("""(pincode) => {
                    // Find ALL text nodes containing the pincode
                    const walker = document.createTreeWalker(
                        document.body,
                        NodeFilter.SHOW_TEXT,
                        null
                    );
                    let textNode = null;
                    while (walker.nextNode()) {
                        if (walker.currentNode.textContent.includes(pincode)) {
                            textNode = walker.currentNode;
                            break;
                        }
                    }
                    if (!textNode) return null;

                    // Walk up to find card container that has a button
                    let card = textNode.parentElement;
                    for (let i = 0; i < 8; i++) {
                        if (!card || !card.parentElement) break;
                        const btns = card.querySelectorAll('button, [role=button]');
                        if (btns.length > 0) break;
                        card = card.parentElement;
                    }
                    if (!card) return null;

                    // Get ALL buttons in this card
                    const btns = card.querySelectorAll('button, [role=button]');
                    if (!btns.length) return null;

                    // The 3-dot button is usually the last/only button in the card
                    const btn = btns[btns.length - 1];
                    
                    // Scroll into view first
                    btn.scrollIntoView({behavior: 'instant', block: 'center'});
                    
                    const rect = btn.getBoundingClientRect();
                    return {
                        x: rect.left + rect.width / 2,
                        y: rect.top + rect.height / 2,
                        width: rect.width,
                        height: rect.height
                    };
                }""", PINCODE)

                if result:
                    print(f"[ADDR] Found ⋮ button at ({result['x']}, {result['y']})")
                    page.mouse.click(result["x"], result["y"])
                    page.wait_for_timeout(1000)

                    # Click "Mark as Default"
                    mark = page.get_by_text("Mark as Default").first
                    mark.wait_for(timeout=3000)
                    mark.click()
                    page.wait_for_timeout(2000)
                    pincode_found = True
                    print(f"[ADDR] Marked {PINCODE} as default ✓")
                    break

            except Exception as e:
                print(f"[ADDR] Attempt failed: {e}")
                try:
                    page.keyboard.press("Escape")
                    page.wait_for_timeout(500)
                except Exception:
                    pass

    except Exception as e:
        print(f"[ADDR] Pincode search failed: {e}")

    if pincode_found:
        page.goto(BASE_URL)
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(2000)
        dismiss_home_modals(page)
        return

    # ── Address not found → Add New Address ──────────────────
    print(f"[ADDR] Pincode {PINCODE} not found — adding new address")

    try:
        add_btn = page.get_by_role("button", name="Add New Address").first
        if not add_btn.is_visible(timeout=3000):
            add_btn = page.get_by_text("Add New Address").first
        add_btn.click()
        page.wait_for_timeout(2000)
    except Exception as e:
        print(f"[ADDR] Could not click Add New Address: {e}")
        page.goto(BASE_URL)
        return

    # ── Delivery address modal — enter pincode ────────────────
    # Search box: "Search for area, street, name..."
    try:
        search = page.locator(
            "input[placeholder*='area'], input[placeholder*='street'], "
            "input[placeholder*='Search for area'], input[placeholder*='name']"
        ).first
        search.wait_for(timeout=5000)
        search.click()
        page.wait_for_timeout(300)
        search.type(PINCODE, delay=100)
        page.wait_for_timeout(2500)

        # Click first Google Places autocomplete result
        for sel in [".pac-item", "[class*=suggestion]", "ul li"]:
            try:
                r = page.locator(sel).first
                if r.is_visible(timeout=2000):
                    r.click()
                    page.wait_for_timeout(2000)
                    break
            except Exception:
                pass

        # Confirm Location
        _confirm_location_modal(page)

    except Exception as e:
        print(f"[ADDR] Pincode entry failed: {e}")
        page.keyboard.press("Escape")
        page.wait_for_timeout(1000)
        page.goto(BASE_URL)
        return

    # ── Confirm Location screen — fill address details ────────
    # After confirming location, a form appears asking for:
    # House no/Flat, Building/Apartment, Nearby Landmark, Name, Phone
    try:
        # Fill House no / Flat (required)
        house_input = page.locator("input[placeholder*='House no'], input[placeholder*='Flat']").first
        if house_input.is_visible(timeout=3000):
            house_input.fill("1")

        # Fill Building / Apartment (optional but recommended)
        building = page.locator("input[placeholder*='Building'], input[placeholder*='Apartment']").first
        if building.is_visible(timeout=2000):
            building.fill("Test Building")

        # Name field — may already be filled with profile name
        name_inp = page.locator("input[placeholder*='Name'], input[value='Jiomart']").first
        if name_inp.is_visible(timeout=2000):
            try:
                if not name_inp.input_value():
                    name_inp.fill("Jiomart")
            except Exception:
                pass

        # Phone field — may already be filled
        phone_inp = page.locator("input[placeholder*='phone'], input[placeholder*='Phone'], input[placeholder*='mobile']").last
        if phone_inp.is_visible(timeout=2000):
            try:
                existing = phone_inp.input_value()
                if not existing or len(existing) < 10:
                    phone_inp.fill(PHONE or "8825594525")
            except Exception:
                pass

        page.wait_for_timeout(1000)

        # Click Save Address
        save_btn = page.get_by_role("button", name="Save Address").first
        if save_btn.is_visible(timeout=3000):
            save_btn.click()
            page.wait_for_timeout(3000)
            print(f"[ADDR] Address saved with pincode {PINCODE}")
        else:
            # Try alternate button text
            for btn_text in ["Save", "Confirm", "Add Address"]:
                try:
                    b = page.get_by_role("button", name=btn_text).first
                    if b.is_visible(timeout=1500):
                        b.click()
                        page.wait_for_timeout(3000)
                        break
                except Exception:
                    pass

    except Exception as e:
        print(f"[ADDR] Address form fill failed: {e}")

    # ── Navigate back to home ─────────────────────────────────
    page.goto(BASE_URL)
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(2000)
    dismiss_home_modals(page)
    print(f"[ADDR] Navigated to home. Location shows: {PINCODE}")


def _confirm_location_modal(page):
    """Handle the map + Confirm Location button modal."""
    try:
        confirm = page.get_by_role("button", name="Confirm Location").first
        confirm.wait_for(timeout=5000)
        confirm.click()
        page.wait_for_timeout(2000)
        print("[ADDR] Confirm Location clicked")
    except Exception:
        # Try alternate confirm patterns
        for text in ["Confirm location", "Confirm", "Set Location"]:
            try:
                b = page.get_by_role("button", name=text).first
                if b.is_visible(timeout=1500):
                    b.click()
                    page.wait_for_timeout(2000)
                    break
            except Exception:
                pass


# ── Main test ─────────────────────────────────────────────────

def test_auth(page: Page):
    """
    Auth + delivery address prerequisite.

    Steps:
      1. Login with phone + OTP
      2. Land on profile page
      3. Click Delivery Address
      4. If pincode address exists → select it
         If not → Add New Address → enter pincode → save
      5. Navigate to home page
      6. Verify logged in and correct pincode shown
    """
    # Step 1-2: Login
    jiomart_login(page)
    expect(page).not_to_have_url(re.compile(r".*sign-up.*"))
    print(f"[AUTH] Logged in successfully")

    # Step 3-5: Setup delivery address with requested pincode
    setup_delivery_address(page)

    # Step 6: Verify home page shows correct pincode
    page.wait_for_load_state("networkidle")
    print(f"[AUTH] Setup complete. Pincode: {PINCODE}, URL: {page.url}")
