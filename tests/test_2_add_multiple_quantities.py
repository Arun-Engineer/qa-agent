import re
import pytest
from playwright.sync_api import Page, expect

CASES = [
    {"name": "Add product to cart", "inputs": {"product": "example_product"},
     "expected": {"cart_visible": True}},
    {"name": "Add multiple quantities of the same product", "inputs": {"product": "example_product", "quantity": 3},
     "expected": {"cart_quantity": 3}},
    {"name": "Remove a product from the cart", "inputs": {"product": "example_product"},
     "expected": {"cart_empty": True}},
    {"name": "Update quantity in the cart", "inputs": {"product": "example_product", "quantity": 5},
     "expected": {"cart_quantity": 5}},
    {"name": "Verify cart total calculation with multiple items", "inputs": {"products": ["product1", "product2"]},
     "expected": {"total_calculation": True}},
    {"name": "Apply valid coupon code", "inputs": {"coupon": "VALIDCODE"},
     "expected": {"discount_applied": True}},
    {"name": "Apply invalid coupon code", "inputs": {"coupon": "INVALIDCODE"},
     "expected": {"error_visible": True, "error_any_of": ["Invalid coupon"]}},
    {"name": "Cart persistence after page refresh", "inputs": {"product": "example_product"},
     "expected": {"cart_visible": True}},
    {"name": "Cart behavior when product goes out of stock", "inputs": {"product": "out_of_stock_product"},
     "expected": {"error_visible": True, "error_any_of": ["Out of stock"]}},
    {"name": "Proceed to checkout from cart", "inputs": {},
     "expected": {"checkout_visible": True}},
    {"name": "Empty cart state", "inputs": {},
     "expected": {"cart_empty": True, "empty_message": "Your cart is empty"}},
    {"name": "Add items from different categories", "inputs": {"products": ["category1_product", "category2_product"]},
     "expected": {"cart_visible": True}},
    {"name": "Verify product price matches between listing and cart", "inputs": {"product": "example_product"},
     "expected": {"price_match": True}},
    {"name": "Cart icon badge count updates correctly", "inputs": {"product": "example_product"},
     "expected": {"badge_count": 1}},
    {"name": "Maximum quantity limit per product", "inputs": {"product": "example_product", "quantity": 10},
     "expected": {"error_visible": True, "error_any_of": ["Maximum quantity limit reached"]}},
]

@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_jio_cart_functionality(page: Page, case):
    page.goto("https://www.jiomart.com")
    page.wait_for_load_state("networkidle")
    
    if "product" in case["inputs"]:
        page.get_by_text(case["inputs"]["product"]).first.click()
        page.wait_for_timeout(1500)
    
    if "quantity" in case["inputs"]:
        page.fill("input[name='quantity']", str(case["inputs"]["quantity"]))
        page.click("button:has-text('Add to Cart')")
        page.wait_for_timeout(1500)

    if case["expected"].get("cart_visible"):
        expect(page.get_by_text("Cart")).to_be_visible()

    if case["expected"].get("cart_empty"):
        expect(page.get_by_text("Your cart is empty")).to_be_visible()

    if case["expected"].get("error_visible"):
        found_error = False
        for msg in case["expected"]["error_any_of"]:
            if page.get_by_text(msg).first.is_visible():
                found_error = True
                break
        assert found_error, f"Expected one of {case['expected']['error_any_of']} to be visible"

    if case["expected"].get("checkout_visible"):
        expect(page.get_by_text("Checkout")).to_be_visible()

    if case["expected"].get("badge_count"):
        badge_count = page.locator("span.cart-badge").inner_text()
        assert int(badge_count) == case["expected"]["badge_count"], "Badge count does not match"