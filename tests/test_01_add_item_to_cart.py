import re
import os
import pytest
from playwright.sync_api import Page, expect

BASE_URL = os.getenv("JIOMART_URL", "https://jiomart.uat.jiomartjcp.com")
PHONE = os.getenv("JIOMART_PHONE", "8825594525")
OTP = os.getenv("JIOMART_OTP", "123456")

def dismiss_modals(page):
    try:
        if page.get_by_text("Select Location Manually").first.is_visible(timeout=3000):
            page.get_by_text("Select Location Manually").first.click()
            page.wait_for_timeout(1500)
    except Exception:
        pass
    try:
        inp = page.locator("input[placeholder*='area'], input[placeholder*='landmark']").first
        if inp.is_visible(timeout=3000):
            inp.type(os.getenv("JIOMART_PINCODE", "400001"), delay=100)
            page.wait_for_timeout(2000)
            page.locator(".pac-item").first.click()
            page.wait_for_timeout(1500)
            page.get_by_role("button", name="Confirm Location").first.click()
            page.wait_for_timeout(2000)
    except Exception:
        pass
    try:
        page.keyboard.press("Escape")
        page.wait_for_timeout(500)
    except Exception:
        pass

def jiomart_login(page):
    if not PHONE:
        pytest.skip("JIOMART_PHONE not set in environment")
    page.goto(BASE_URL)
    page.wait_for_load_state("networkidle")
    dismiss_modals(page)
    for sel in ["[class*='login']", "[aria-label*='login']", "text=Login", "[class*='user']"]:
        try:
            b = page.locator(sel).first
            if b.is_visible(timeout=2000):
                b.click()
                page.wait_for_timeout(1500)
                break
        except Exception:
            pass
    ph = page.locator("input[type='tel'], input[placeholder*='mobile'], input[placeholder*='Mobile']").first
    ph.wait_for(timeout=5000)
    ph.fill(PHONE)
    for btn in ["Get OTP", "Continue", "Send OTP"]:
        try:
            b = page.get_by_role("button", name=btn).first
            if b.is_visible(timeout=2000):
                b.click()
                page.wait_for_timeout(2000)
                break
        except Exception:
            pass
    otp_inp = page.locator("input[placeholder*='OTP'], input[placeholder*='otp']").first
    otp_inp.wait_for(timeout=8000)
    otp_inp.fill(OTP)
    for btn in ["Verify OTP", "Verify", "Submit", "Confirm"]:
        try:
            b = page.get_by_role("button", name=btn).first
            if b.is_visible(timeout=2000):
                b.click()
                page.wait_for_timeout(3000)
                break
        except Exception:
            pass

def test_add_item_to_cart(page: Page):
    jiomart_login(page)
    page.goto(BASE_URL)
    page.wait_for_load_state("networkidle")
    dismiss_modals(page)
    page.locator(SEARCH).fill("item name")  # Replace with actual item name
    page.locator(SEARCH).press("Enter")
    page.wait_for_load_state("networkidle")
    page.locator(ADD_BTN).first.click()
    page.wait_for_timeout(1500)
    expect(page.locator(CART_COUNT)).to_have_text("1")  # Adjust expected count as necessary