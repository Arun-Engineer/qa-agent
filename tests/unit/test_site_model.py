"""Unit tests for SiteModel — serialization, load, summary."""
import json
import os
import tempfile
import pytest
from src.discovery.site_model import SiteModel, PageInfo, ComponentInfo, ApiEndpoint


def _make_model() -> SiteModel:
    return SiteModel(
        base_url="https://example.com",
        pages=[
            PageInfo(
                url="https://example.com",
                title="Home",
                page_type="home",
                classification_confidence=0.9,
                classification_method="heuristic",
                status_code=200,
                depth=0,
                components=[
                    ComponentInfo(
                        component_type="buttons",
                        selector="#login-btn",
                        tag="button",
                        text="Log In",
                        is_interactive=True,
                    ),
                    ComponentInfo(
                        component_type="inputs",
                        selector='input[name="search"]',
                        tag="input",
                        text="",
                        is_interactive=True,
                    ),
                ],
                outgoing_links=["https://example.com/products", "https://example.com/about"],
            ),
            PageInfo(
                url="https://example.com/products",
                title="Products",
                page_type="plp",
                classification_confidence=0.8,
                status_code=200,
                depth=1,
                parent_url="https://example.com",
            ),
            PageInfo(
                url="https://example.com/cart",
                title="Cart",
                page_type="cart",
                classification_confidence=0.85,
                status_code=200,
                depth=1,
            ),
        ],
        api_endpoints=[
            ApiEndpoint(method="GET", url="https://example.com/api/products", path="/api/products", status_code=200),
            ApiEndpoint(method="POST", url="https://example.com/api/cart", path="/api/cart", status_code=201),
        ],
        crawl_strategy="bfs",
        total_duration_seconds=12.5,
    )


class TestSiteModel:

    def test_page_type_counts(self):
        model = _make_model()
        counts = model.page_type_counts
        assert counts["home"] == 1
        assert counts["plp"] == 1
        assert counts["cart"] == 1

    def test_summary_contains_key_info(self):
        model = _make_model()
        summary = model.summary
        assert "example.com" in summary
        assert "Pages: 3" in summary
        assert "API endpoints: 2" in summary

    def test_to_dict_structure(self):
        model = _make_model()
        d = model.to_dict()
        assert d["base_url"] == "https://example.com"
        assert d["pages_count"] == 3
        assert d["api_endpoints_count"] == 2
        assert len(d["pages"]) == 3
        assert len(d["api_endpoints"]) == 2
        # Verify components serialized
        home_page = d["pages"][0]
        assert len(home_page["components"]) == 2
        assert home_page["components"][0]["component_type"] == "buttons"

    def test_save_and_load_roundtrip(self):
        model = _make_model()
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
            path = f.name

        try:
            saved_path = model.save(path)
            assert os.path.exists(saved_path)

            loaded = SiteModel.load(saved_path)
            assert loaded.base_url == model.base_url
            assert len(loaded.pages) == len(model.pages)
            assert len(loaded.api_endpoints) == len(model.api_endpoints)
            assert loaded.pages[0].page_type == "home"
            assert loaded.pages[0].components[0].text == "Log In"
            assert loaded.api_endpoints[0].method == "GET"
        finally:
            os.unlink(path)

    def test_empty_model(self):
        model = SiteModel(base_url="https://empty.test")
        assert len(model.pages) == 0
        assert len(model.api_endpoints) == 0
        d = model.to_dict()
        assert d["pages_count"] == 0

    def test_save_creates_directory(self):
        model = _make_model()
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "deep", "nested", "model.json")
            saved = model.save(path)
            assert os.path.exists(saved)
            with open(saved) as f:
                data = json.load(f)
            assert data["base_url"] == "https://example.com"


class TestPageInfo:

    def test_defaults(self):
        p = PageInfo(url="https://test.com/page")
        assert p.page_type == "unknown"
        assert p.depth == 0
        assert p.components == []
        assert p.outgoing_links == []

    def test_to_dict(self):
        p = PageInfo(url="https://test.com", title="Test", page_type="home")
        d = p.to_dict()
        assert d["url"] == "https://test.com"
        assert d["page_type"] == "home"


class TestApiEndpoint:

    def test_to_dict(self):
        ep = ApiEndpoint(method="POST", url="https://api.test.com/v1/users", path="/v1/users", status_code=201)
        d = ep.to_dict()
        assert d["method"] == "POST"
        assert d["status_code"] == 201


class TestComponentInfo:

    def test_to_dict(self):
        c = ComponentInfo(
            component_type="buttons",
            selector="#btn",
            tag="button",
            text="Click Me",
            is_interactive=True,
        )
        d = c.to_dict()
        assert d["component_type"] == "buttons"
        assert d["is_interactive"] is True
