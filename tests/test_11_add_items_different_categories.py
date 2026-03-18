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
     "expected": {"error_visible": True}},
    {"name": "cart_persistence_after_refresh", "inputs": {},
     "expected": {"cart_count": 1}},
    {"name": "cart_behavior_out_of_stock", "inputs": {"product": "out_of_stock_product"},
     "expected": {"error_visible": True}},
    {"name": "proceed_to_checkout", "inputs": {},
     "expected": {"url_checkout": True}},
    {"name": "empty_cart_state", "inputs": {},
     "expected": {"empty_message_visible": True}},
    {"name": "add_items_from_different_categories", "inputs": {"categories": ["category1", "category2"]},
     "expected": {"cart_count": 2}},
    {"name": "verify_product_price_match", "inputs": {"product": "example_product"},
     "expected": {"price_match": True}},
    {"name": "cart_icon_badge_count_updates", "inputs": {},
     "expected": {"badge_count": 1}},
    {"name": "maximum_quantity_limit", "inputs": {"product": "example_product", "quantity": 10},
     "expected": {"error_visible": True}},
]

@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_jio_cart_functionality(page: Page, case):
    page.goto("https://www.jiomart.com")
    page.wait_for_load_state("networkidle")
    
    if case["name"] == "add_product_to_cart":
        page.get_by_text(case["inputs"]["product"]).click()
        page.get_by_text("Add to Cart").click()
        assert expect(page.get_by_text("Cart (1)")).to_be_visible()
    
    elif case["name"] == "add_multiple_quantities":
        page.get_by_text(case["inputs"]["product"]).click()
        page.fill("input[type='number']", str(case["inputs"]["quantity"]))
        page.get_by_text("Add to Cart").click()
        assert expect(page.get_by_text(f"Cart ({case['inputs']['quantity']})")).to_be_visible()
    
    elif case["name"] == "remove_product_from_cart":
        page.goto("https://www.jiomart.com/cart")
        page.get_by_text("Remove").click()
        assert expect(page.get_by_text("Your cart is empty")).to_be_visible()
    
    elif case["name"] == "update_quantity_in_cart":
        page.goto("https://www.jiomart.com/cart")
        page.fill("input[type='number']", str(case["inputs"]["new_quantity"]))
        page.get_by_text("Update").click()
        assert expect(page.get_by_text(f"Cart ({case['inputs']['new_quantity']})")).to_be_visible()
    
    elif case["name"] == "verify_cart_total_calculation":
        page.goto("https://www.jiomart.com/cart")
        total = sum([get_product_price(product) for product in case["inputs"]["products"]])
        assert expect(page.get_by_text(f"Total: {total}")).to_be_visible()
    
    elif case["name"] == "apply_coupon_valid":
        page.goto("https://www.jiomart.com/cart")
        page.fill("input[name='coupon']", case["inputs"]["coupon"])
        page.get_by_text("Apply").click()
        assert expect(page.get_by_text("Coupon applied successfully")).to_be_visible()
    
    elif case["name"] == "apply_coupon_invalid":
        page.goto("https://www.jiomart.com/cart")
        page.fill("input[name='coupon']", case["inputs"]["coupon"])
        page.get_by_text("Apply").click()
        assert expect(page.get_by_text("Invalid coupon code")).to_be_visible()
    
    elif case["name"] == "cart_persistence_after_refresh":
        page.goto("https://www.jiomart.com/cart")
        page.get_by_text("Add to Cart").click()
        page.reload()
        assert expect(page.get_by_text("Cart (1)")).to_be_visible()
    
    elif case["name"] == "cart_behavior_out_of_stock":
        page.goto("https://www.jiomart.com")
        page.get_by_text(case["inputs"]["product"]).click()
        assert expect(page.get_by_text("Out of stock")).to_be_visible()
    
    elif case["name"] == "proceed_to_checkout":
        page.goto("https://www.jiomart.com/cart")
        page.get_by_text("Checkout").click()
        assert expect(page).to_have_url(re.compile(r".*checkout.*"))
    
    elif case["name"] == "empty_cart_state":
        page.goto("https://www.jiomart.com/cart")
        assert expect(page.get_by_text("Your cart is empty")).to_be_visible()
    
    elif case["name"] == "add_items_from_different_categories":
        for category in case["inputs"]["categories"]:
            page.goto(f"https://www.jiomart.com/{category}")
            page.get_by_text("Add to Cart").click()
        assert expect(page.get_by_text(f"Cart ({len(case['inputs']['categories'])})")).to_be_visible()
    
    elif case["name"] == "verify_product_price_match":
        page.goto("https://www.jiomart.com")
        page.get_by_text(case["inputs"]["product"]).click()
        price_listing = get_product_price(case["inputs"]["product"])
        page.get_by_text("Add to Cart").click()
        page.goto("https://www.jiomart.com/cart")
        price_cart = get_cart_product_price(case["inputs"]["product"])
        assert price_listing == price_cart
    
    elif case["name"] == "cart_icon_badge_count_updates":
        page.goto("https://www.jiomart.com")
        page.get_by_text("Add to Cart").click()
        assert expect(page.get_by_text("Cart (1)")).to_be_visible()
    
    elif case["name"] == "maximum_quantity_limit":
        page.goto("https://www.jiomart.com")
        page.get_by_text(case["inputs"]["product"]).click()
        page.fill("input[type='number']", str(case["inputs"]["quantity"]))
        page.get_by_text("Add to Cart").click()
        assert expect(page.get_by_text("Maximum quantity limit reached")).to_be_visible()