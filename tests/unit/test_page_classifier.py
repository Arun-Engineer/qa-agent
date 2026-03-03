"""Unit tests for Page Classifier — heuristic URL + title/meta classification."""
import pytest
from src.discovery.page_classifier import classify_page, _classify_by_url, _classify_by_title_meta
from src.discovery.site_model import PageInfo


class TestUrlClassification:

    @pytest.mark.parametrize("url,expected", [
        ("https://shop.com/", "home"),
        ("https://shop.com", "home"),
        ("https://shop.com/login", "login"),
        ("https://shop.com/auth/signin", "login"),
        ("https://shop.com/signup", "signup"),
        ("https://shop.com/cart", "cart"),
        ("https://shop.com/basket", "cart"),
        ("https://shop.com/checkout", "checkout"),
        ("https://shop.com/checkout/payment", "checkout"),
        ("https://shop.com/search?q=shoes", "search"),
        ("https://shop.com/product/abc-123", "pdp"),
        ("https://shop.com/p/nike-air", "pdp"),
        ("https://shop.com/category/shoes", "plp"),
        ("https://shop.com/products", "plp"),
        ("https://shop.com/shop/mens", "plp"),
        ("https://shop.com/account", "settings"),
        ("https://shop.com/dashboard", "dashboard"),
        ("https://shop.com/blog/ai-testing", "blog"),
        ("https://shop.com/about", "about"),
        ("https://shop.com/faq", "faq"),
        ("https://shop.com/404", "error"),
        ("https://shop.com/terms", "legal"),
    ])
    def test_url_patterns(self, url, expected):
        result = _classify_by_url(url)
        assert result is not None, f"Expected {expected} for {url}"
        assert result.page_type == expected, f"URL {url}: expected {expected}, got {result.page_type}"
        assert result.confidence >= 0.7

    def test_unknown_url_returns_none(self):
        result = _classify_by_url("https://shop.com/some-random-path")
        assert result is None


class TestTitleMetaClassification:

    @pytest.mark.parametrize("title,expected", [
        ("Shopping Cart - MyStore", "cart"),
        ("Checkout - Complete your order", "checkout"),
        ("Log In to your account", "login"),
        ("Sign Up for free", "signup"),
        ("Search results for: shoes", "search"),
        ("Admin Dashboard", "dashboard"),
        ("404 - Page not found", "error"),
    ])
    def test_title_based(self, title, expected):
        page = PageInfo(url="https://shop.com/x", title=title)
        result = _classify_by_title_meta(page)
        assert result is not None
        assert result.page_type == expected


class TestFullClassification:

    def test_url_wins_for_clear_patterns(self):
        page = PageInfo(url="https://shop.com/login", title="Welcome")
        result = classify_page(page)
        assert result.page_type == "login"
        assert result.confidence >= 0.8

    def test_fallback_to_title(self):
        page = PageInfo(url="https://shop.com/custom-page", title="Shopping Cart - MyStore")
        result = classify_page(page)
        assert result.page_type == "cart"

    def test_truly_unknown(self):
        page = PageInfo(url="https://shop.com/xyz", title="Something")
        result = classify_page(page)
        assert result.page_type == "unknown"
        assert result.confidence == 0.0

    def test_classification_has_evidence(self):
        page = PageInfo(url="https://shop.com/cart")
        result = classify_page(page)
        assert len(result.evidence) > 0

    def test_method_is_heuristic_without_page(self):
        page = PageInfo(url="https://shop.com/checkout")
        result = classify_page(page, page=None, use_llm=False)
        assert result.method in ("heuristic", "combined")
