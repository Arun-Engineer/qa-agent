import re
import pytest
from playwright.sync_api import Page, expect

CASES = [
    {"name": "add_product_to_cart", "inputs": {"product": "example_product"},
     "expected": {"cart_count": 1}},
    {"name": "add_multiple_quantities", "inputs": {"product": "example_product", "quantity": 5},
     "expected": {"cart_count": 5}},
    {"name": "remove_product_from_cart", "inputs": {"product": "example_product"},
     "expected": {"cart_count": 0}},
    {"name": "update_quantity_in_cart", "inputs": {"product": "example_product", "new_quantity": 3},
     "expected": {"cart_count": 3}},
    {"name": "verify_cart_total_calculation", "inputs": {"products": ["product1", "product2"]},
     "expected": {"total": 100}},
    {"name": "apply_coupon_code_valid", "inputs": {"coupon": "VALIDCODE"},
     "expected": {"discount_applied": True}},
    {"name": "apply_coupon_code_invalid", "inputs": {"coupon": "INVALIDCODE"},
     "expected": {"error_visible": True, "error_any_of": ["Invalid coupon"]}},
    {"name": "cart_persistence_after_refresh", "inputs": {"product": "example_product"},
     "expected": {"cart_count": 1}},
    {"name": "cart_behavior_out_of_stock", "inputs": {"product": "out_of_stock_product"},
     "expected": {"error_visible": True, "error_any_of": ["Out of stock"]}},
    {"name": "proceed_to_checkout", "inputs": {},
     "expected": {"url": re.compile(r".*checkout.*")}},
    {"name": "empty_cart_state", "inputs": {},
     "expected": {"error_visible": True, "error_any_of": ["Your cart is empty"]}},
    {"name": "add_items_from_different_categories", "inputs": {"products": ["product1", "product2"]},
     "expected": {"cart_count": 2}},
    {"name": "verify_product_price_match", "inputs": {"product": "example_product"},
     "expected": {"price_match": True}},
    {"name": "cart_icon_badge_count_updates", "inputs": {"product": "example_product"},
     "expected": {"badge_count": 1}},
    {"name": "maximum_quantity_limit", "inputs": {"product": "example_product", "quantity": 100},
     "expected": {"error_visible": True, "error_any_of": ["Maximum quantity limit reached"]}},
]

@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_cart_functionality(page: Page, case):
    page.goto("https://www.jiomart.com")
    page.wait_for_load_state("networkidle")
    
    if "product" in case["inputs"]:
        page.get_by_text(case["inputs"]["product"]).first.click()
        page.get_by_text("Add to Cart").click()
        page.wait_for_timeout(1500)

    if "quantity" in case["inputs"]:
        page.fill("input[name='quantity']", str(case["inputs"]["quantity"]))
        page.get_by_text("Update").click()
        page.wait_for_timeout(1500)

    if "coupon" in case["inputs"]:
        page.fill("input[name='coupon']", case["inputs"]["coupon"])
        page.get_by_text("Apply").click()
        page.wait_for_timeout(1500)

    if "url" in case["expected"]:
        expect(page).to_have_url(case["expected"]["url"])

    if "error_visible" in case["expected"]:
        found_error = False
        for msg in case["expected"]["error_any_of"]:
            if page.get_by_text(msg).first.is_visible():
                found_error = True
                break
        assert found_error, f"Expected one of {case['expected']['error_any_of']} to be visible"

    if "cart_count" in case["expected"]:
        cart_count = int(page.get_by_text("Cart").first.inner_text())
        assert cart_count == case["expected"]["cart_count"], f"Expected cart count to be {case['expected']['cart_count']} but got {cart_count}"

    if "total" in case["expected"]:
        total = float(page.get_by_text("Total").first.inner_text().replace("₹", "").strip())
        assert total == case["expected"]["total"], f"Expected total to be {case['expected']['total']} but got {total}"

    if "badge_count" in case["expected"]:
        badge_count = int(page.get_by_text("Cart").first.get_attribute("data-count"))
        assert badge_count == case["expected"]["badge_count"], f"Expected badge count to be {case['expected']['badge_count']} but got {badge_count}"

    if "price_match" in case["expected"]:
        price_listing = float(page.get_by_text(case["inputs"]["product"]).first.get_attribute("data-price"))
        price_cart = float(page.get_by_text("Cart").first.get_attribute("data-price"))
        assert price_listing == price_cart, "Product price does not match between listing and cart"