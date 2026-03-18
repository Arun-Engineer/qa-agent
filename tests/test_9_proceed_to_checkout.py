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
    {"name": "apply_coupon_valid", "inputs": {"coupon": "VALIDCODE"},
     "expected": {"discount_applied": True}},
    {"name": "apply_coupon_invalid", "inputs": {"coupon": "INVALIDCODE"},
     "expected": {"discount_applied": False}},
    {"name": "cart_persistence_after_refresh", "inputs": {"product": "example_product"},
     "expected": {"cart_count": 1}},
    {"name": "cart_behavior_out_of_stock", "inputs": {"product": "out_of_stock_product"},
     "expected": {"error_visible": True}},
    {"name": "proceed_to_checkout", "inputs": {},
     "expected": {"url": re.compile(r".*checkout.*")}},
    {"name": "empty_cart_state", "inputs": {},
     "expected": {"error_visible": True}},
    {"name": "add_items_from_different_categories", "inputs": {"categories": ["category1", "category2"]},
     "expected": {"cart_count": 2}},
    {"name": "verify_product_price_match", "inputs": {"product": "example_product"},
     "expected": {"price_match": True}},
    {"name": "cart_icon_badge_count", "inputs": {},
     "expected": {"badge_count": 1}},
    {"name": "maximum_quantity_limit", "inputs": {"product": "example_product", "quantity": 10},
     "expected": {"limit_exceeded": True}},
]

@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_cart_functionality(page: Page, case):
    page.goto("https://www.jiomart.com")
    page.wait_for_load_state("networkidle")
    
    if case["name"] == "add_product_to_cart":
        page.get_by_text(case["inputs"]["product"]).first.click()
        page.get_by_text("Add to Cart").click()
        assert page.get_by_text("Cart").first.is_visible()
        assert page.get_by_text(str(case["expected"]["cart_count"])).first.is_visible()

    elif case["name"] == "add_multiple_quantities":
        page.get_by_text(case["inputs"]["product"]).first.click()
        page.fill("input[name='quantity']", str(case["inputs"]["quantity"]))
        page.get_by_text("Add to Cart").click()
        assert page.get_by_text(str(case["expected"]["cart_count"])).first.is_visible()

    elif case["name"] == "remove_product_from_cart":
        page.get_by_text(case["inputs"]["product"]).first.click()
        page.get_by_text("Remove").click()
        assert page.get_by_text("Your cart is empty").first.is_visible()

    elif case["name"] == "update_quantity_in_cart":
        page.get_by_text(case["inputs"]["product"]).first.click()
        page.fill("input[name='quantity']", str(case["inputs"]["new_quantity"]))
        page.get_by_text("Update").click()
        assert page.get_by_text(str(case["expected"]["cart_count"])).first.is_visible()

    elif case["name"] == "verify_cart_total_calculation":
        for product in case["inputs"]["products"]:
            page.get_by_text(product).first.click()
            page.get_by_text("Add to Cart").click()
        assert page.get_by_text(str(case["expected"]["total"])).first.is_visible()

    elif case["name"] == "apply_coupon_valid":
        page.fill("input[name='coupon']", case["inputs"]["coupon"])
        page.get_by_text("Apply").click()
        assert page.get_by_text("Coupon applied").first.is_visible()

    elif case["name"] == "apply_coupon_invalid":
        page.fill("input[name='coupon']", case["inputs"]["coupon"])
        page.get_by_text("Apply").click()
        assert page.get_by_text("Invalid coupon").first.is_visible()

    elif case["name"] == "cart_persistence_after_refresh":
        page.get_by_text(case["inputs"]["product"]).first.click()
        page.get_by_text("Add to Cart").click()
        page.reload()
        assert page.get_by_text(str(case["expected"]["cart_count"])).first.is_visible()

    elif case["name"] == "cart_behavior_out_of_stock":
        page.get_by_text(case["inputs"]["product"]).first.click()
        assert page.get_by_text("Out of stock").first.is_visible()

    elif case["name"] == "proceed_to_checkout":
        page.get_by_text("Cart").first.click()
        page.get_by_text("Proceed to Checkout").click()
        expect(page).to_have_url(case["expected"]["url"])

    elif case["name"] == "empty_cart_state":
        page.get_by_text("Cart").first.click()
        assert page.get_by_text("Your cart is empty").first.is_visible()

    elif case["name"] == "add_items_from_different_categories":
        for category in case["inputs"]["categories"]:
            page.get_by_text(category).first.click()
            page.get_by_text("Add to Cart").click()
        assert page.get_by_text(str(case["expected"]["cart_count"])).first.is_visible()

    elif case["name"] == "verify_product_price_match":
        page.get_by_text(case["inputs"]["product"]).first.click()
        price_listing = page.get_by_text("Price").first.inner_text()
        page.get_by_text("Cart").first.click()
        price_cart = page.get_by_text(case["inputs"]["product"]).first.inner_text()
        assert price_listing == price_cart

    elif case["name"] == "cart_icon_badge_count":
        page.get_by_text("Cart").first.click()
        assert page.get_by_text(str(case["expected"]["badge_count"])).first.is_visible()

    elif case["name"] == "maximum_quantity_limit":
        page.get_by_text(case["inputs"]["product"]).first.click()
        page.fill("input[name='quantity']", str(case["inputs"]["quantity"]))
        page.get_by_text("Add to Cart").click()
        assert page.get_by_text("Maximum quantity exceeded").first.is_visible()