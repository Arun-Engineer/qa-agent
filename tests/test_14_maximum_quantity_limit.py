import re
import pytest
from playwright.sync_api import Page, expect

CASES = [
    {"name": "add_product_to_cart", "inputs": {"product": "example_product"},
     "expected": {"error_visible": False, "error_any_of": []}},
    {"name": "add_multiple_quantities", "inputs": {"product": "example_product", "quantity": 5},
     "expected": {"error_visible": False, "error_any_of": []}},
    {"name": "remove_product_from_cart", "inputs": {"product": "example_product"},
     "expected": {"error_visible": False, "error_any_of": []}},
    {"name": "update_quantity_in_cart", "inputs": {"product": "example_product", "quantity": 3},
     "expected": {"error_visible": False, "error_any_of": []}},
    {"name": "verify_cart_total_calculation", "inputs": {"products": ["product1", "product2"]},
     "expected": {"error_visible": False, "error_any_of": []}},
    {"name": "apply_coupon_code", "inputs": {"code": "VALID_CODE"},
     "expected": {"error_visible": False, "error_any_of": []}},
    {"name": "cart_persistence_after_refresh", "inputs": {},
     "expected": {"error_visible": False, "error_any_of": []}},
    {"name": "cart_behavior_out_of_stock", "inputs": {"product": "out_of_stock_product"},
     "expected": {"error_visible": True, "error_any_of": ["Product out of stock"]}},
    {"name": "proceed_to_checkout", "inputs": {},
     "expected": {"error_visible": False, "error_any_of": []}},
    {"name": "empty_cart_state", "inputs": {},
     "expected": {"error_visible": True, "error_any_of": ["Your cart is empty"]}},
    {"name": "add_items_from_different_categories", "inputs": {"categories": ["category1", "category2"]},
     "expected": {"error_visible": False, "error_any_of": []}},
    {"name": "verify_product_price_match", "inputs": {"product": "example_product"},
     "expected": {"error_visible": False, "error_any_of": []}},
    {"name": "cart_icon_badge_count_updates", "inputs": {},
     "expected": {"error_visible": False, "error_any_of": []}},
    {"name": "maximum_quantity_limit", "inputs": {"product": "example_product", "quantity": 100},
     "expected": {"error_visible": True, "error_any_of": ["Maximum quantity limit reached"]}},
]

@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_jio_cart_functionality(page: Page, case):
    page.goto("https://www.jiomart.com")
    page.wait_for_load_state("networkidle")
    
    if case["name"] == "add_product_to_cart":
        page.get_by_text(case["inputs"]["product"]).first.click()
        page.get_by_text("Add to Cart").first.click()
    
    elif case["name"] == "add_multiple_quantities":
        page.get_by_text(case["inputs"]["product"]).first.click()
        page.fill("input[type='number']", str(case["inputs"]["quantity"]))
        page.get_by_text("Add to Cart").first.click()
    
    elif case["name"] == "remove_product_from_cart":
        page.get_by_text("Cart").first.click()
        page.get_by_text(case["inputs"]["product"]).first.hover()
        page.get_by_text("Remove").first.click()
    
    elif case["name"] == "update_quantity_in_cart":
        page.get_by_text("Cart").first.click()
        page.fill("input[type='number']", str(case["inputs"]["quantity"]))
    
    elif case["name"] == "verify_cart_total_calculation":
        page.get_by_text("Cart").first.click()
        # Add logic to verify total calculation
    
    elif case["name"] == "apply_coupon_code":
        page.get_by_text("Cart").first.click()
        page.fill("input[name='coupon']", case["inputs"]["code"])
        page.get_by_text("Apply").first.click()
    
    elif case["name"] == "cart_persistence_after_refresh":
        page.get_by_text("Cart").first.click()
        page.reload()
    
    elif case["name"] == "cart_behavior_out_of_stock":
        page.get_by_text(case["inputs"]["product"]).first.click()
        page.get_by_text("Add to Cart").first.click()
    
    elif case["name"] == "proceed_to_checkout":
        page.get_by_text("Cart").first.click()
        page.get_by_text("Checkout").first.click()
    
    elif case["name"] == "empty_cart_state":
        page.get_by_text("Cart").first.click()
        page.get_by_text("Empty Cart").first.click()
    
    elif case["name"] == "add_items_from_different_categories":
        for category in case["inputs"]["categories"]:
            page.get_by_text(category).first.click()
            page.get_by_text("Add to Cart").first.click()
    
    elif case["name"] == "verify_product_price_match":
        page.get_by_text(case["inputs"]["product"]).first.click()
        # Add logic to verify price match
    
    elif case["name"] == "cart_icon_badge_count_updates":
        page.get_by_text("Cart").first.click()
        # Add logic to verify badge count
    
    elif case["name"] == "maximum_quantity_limit":
        page.get_by_text(case["inputs"]["product"]).first.click()
        page.fill("input[type='number']", str(case["inputs"]["quantity"]))
        page.get_by_text("Add to Cart").first.click()
    
    page.wait_for_timeout(1500)
    
    found_error = False
    for msg in case["expected"]["error_any_of"]:
        if page.get_by_text(msg).first.is_visible():
            found_error = True
            break
    assert found_error == case["expected"]["error_visible"], f"Expected one of {case['expected']['error_any_of']} to be visible"
    expect(page).to_have_url(re.compile(r".*cart.*"))