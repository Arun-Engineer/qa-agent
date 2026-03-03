"""Unit tests for API Surface Mapper — URL filtering logic."""
import pytest
from src.discovery.api_surface_mapper import _should_ignore, _is_api_call


class TestUrlFiltering:

    @pytest.mark.parametrize("url", [
        "https://cdn.example.com/style.css",
        "https://site.com/image.png",
        "https://site.com/photo.jpg",
        "https://site.com/font.woff2",
        "https://site.com/icon.svg",
        "https://www.google-analytics.com/collect",
        "https://www.googletagmanager.com/gtm.js",
        "https://connect.facebook.com/signals",
    ])
    def test_ignore_static_and_tracking(self, url):
        assert _should_ignore(url), f"Should ignore: {url}"

    @pytest.mark.parametrize("url", [
        "https://api.example.com/v1/products",
        "https://site.com/api/users",
        "https://site.com/graphql",
    ])
    def test_not_ignored_api(self, url):
        assert not _should_ignore(url), f"Should NOT ignore: {url}"


class TestIsApiCall:

    def test_xhr_is_api(self):
        assert _is_api_call("https://any.com/anything", "xhr")

    def test_fetch_is_api(self):
        assert _is_api_call("https://any.com/anything", "fetch")

    def test_api_path_is_api(self):
        assert _is_api_call("https://site.com/api/products", "document")
        assert _is_api_call("https://site.com/v1/users", "other")
        assert _is_api_call("https://site.com/graphql", "other")

    def test_regular_document_not_api(self):
        assert not _is_api_call("https://site.com/about", "document")
