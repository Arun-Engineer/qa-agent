"""Unit tests for crawler URL normalization."""
import pytest
from src.discovery.site_crawler import _normalize_url


class TestNormalizeUrl:

    BASE = "https://shop.example.com"

    def test_relative_url(self):
        assert _normalize_url("/products", self.BASE) == "https://shop.example.com/products"

    def test_absolute_same_origin(self):
        assert _normalize_url("https://shop.example.com/cart", self.BASE) == "https://shop.example.com/cart"

    def test_strips_fragment(self):
        result = _normalize_url("https://shop.example.com/page#section", self.BASE)
        assert "#" not in result

    def test_rejects_cross_origin(self):
        assert _normalize_url("https://evil.com/hack", self.BASE) is None

    def test_rejects_mailto(self):
        assert _normalize_url("mailto:test@test.com", self.BASE) is None

    def test_rejects_javascript(self):
        assert _normalize_url("javascript:void(0)", self.BASE) is None

    def test_rejects_binary_files(self):
        assert _normalize_url("/files/report.pdf", self.BASE) is None
        assert _normalize_url("/img/photo.jpg", self.BASE) is None
        assert _normalize_url("/dl/app.exe", self.BASE) is None

    def test_empty_returns_none(self):
        assert _normalize_url("", self.BASE) is None
        assert _normalize_url("  ", self.BASE) is None

    def test_preserves_query_params(self):
        result = _normalize_url("/search?q=test&page=2", self.BASE)
        assert "q=test" in result
        assert "page=2" in result

    def test_trailing_slash_normalized(self):
        result = _normalize_url("/products/", self.BASE)
        assert result == "https://shop.example.com/products"
