"""Site Model — Central data structure for discovered site topology.

The SiteModel is the OUTPUT of the discovery engine and the INPUT
for cognitive agents in Phase 3. It serializes to JSON and (Phase 4) Neo4j.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional


@dataclass
class ComponentInfo:
    """A discovered UI component on a page."""
    component_type: str              # button, input, form, nav, card, media, link, etc.
    selector: str                    # CSS selector or XPath
    tag: str = ""                    # HTML tag name
    text: str = ""                   # visible text / label (truncated)
    attributes: dict = field(default_factory=dict)  # id, class, name, role, etc.
    is_interactive: bool = False
    is_visible: bool = True
    bounding_box: Optional[dict] = None  # {x, y, width, height}

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ApiEndpoint:
    """A captured XHR/Fetch API call."""
    method: str                      # GET, POST, PUT, DELETE, PATCH
    url: str                         # full URL
    path: str = ""                   # URL path without domain
    status_code: Optional[int] = None
    content_type: Optional[str] = None
    request_headers: dict = field(default_factory=dict)
    response_headers: dict = field(default_factory=dict)
    triggered_from: Optional[str] = None  # page URL that triggered this
    timing_ms: Optional[float] = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class PageInfo:
    """A discovered page with its classification and components."""
    url: str
    title: str = ""
    page_type: str = "unknown"       # home, plp, pdp, cart, checkout, login, search, form, dashboard, error, other, unknown
    classification_confidence: float = 0.0
    classification_method: str = ""  # heuristic | llm | combined
    status_code: int = 200
    depth: int = 0                   # how many clicks from start page
    parent_url: Optional[str] = None
    screenshot_path: Optional[str] = None
    components: list[ComponentInfo] = field(default_factory=list)
    outgoing_links: list[str] = field(default_factory=list)
    meta: dict = field(default_factory=dict)  # og:tags, description, etc.
    discovered_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    def to_dict(self) -> dict:
        d = asdict(self)
        d["components"] = [c.to_dict() if hasattr(c, "to_dict") else c for c in self.components]
        return d


@dataclass
class SiteModel:
    """Complete site topology — pages + components + API surface."""
    base_url: str
    pages: list[PageInfo] = field(default_factory=list)
    api_endpoints: list[ApiEndpoint] = field(default_factory=list)
    crawl_strategy: str = "bfs"
    crawl_start: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    crawl_end: Optional[str] = None
    total_duration_seconds: Optional[float] = None
    errors: list[dict] = field(default_factory=list)

    # ── Summary helpers ──────────────────────────────────────

    @property
    def page_type_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for p in self.pages:
            counts[p.page_type] = counts.get(p.page_type, 0) + 1
        return counts

    @property
    def summary(self) -> str:
        lines = [
            f"Site: {self.base_url}",
            f"Pages: {len(self.pages)}",
            f"API endpoints: {len(self.api_endpoints)}",
            f"Page types: {self.page_type_counts}",
            f"Errors: {len(self.errors)}",
        ]
        if self.total_duration_seconds:
            lines.append(f"Duration: {self.total_duration_seconds:.1f}s")
        return "\n".join(lines)

    # ── Serialization ────────────────────────────────────────

    def to_dict(self) -> dict:
        return {
            "base_url": self.base_url,
            "crawl_strategy": self.crawl_strategy,
            "crawl_start": self.crawl_start,
            "crawl_end": self.crawl_end,
            "total_duration_seconds": self.total_duration_seconds,
            "pages_count": len(self.pages),
            "api_endpoints_count": len(self.api_endpoints),
            "page_type_counts": self.page_type_counts,
            "pages": [p.to_dict() for p in self.pages],
            "api_endpoints": [ep.to_dict() for ep in self.api_endpoints],
            "errors": self.errors,
        }

    def save(self, path: str = None) -> str:
        """Save site model to JSON. Returns the file path."""
        if path is None:
            out_dir = Path(os.getenv("DISCOVERY_OUTPUT_DIR", "data/discovery"))
            out_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            domain = self.base_url.split("//")[-1].split("/")[0].replace(":", "_")
            path = str(out_dir / f"site_model_{domain}_{ts}.json")

        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)
        return path

    @classmethod
    def load(cls, path: str) -> SiteModel:
        """Load site model from JSON file."""
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        pages = []
        for p in data.get("pages", []):
            components = [ComponentInfo(**c) for c in p.pop("components", [])]
            pages.append(PageInfo(**p, components=components))

        api_endpoints = [ApiEndpoint(**ep) for ep in data.get("api_endpoints", [])]

        return cls(
            base_url=data["base_url"],
            pages=pages,
            api_endpoints=api_endpoints,
            crawl_strategy=data.get("crawl_strategy", "bfs"),
            crawl_start=data.get("crawl_start", ""),
            crawl_end=data.get("crawl_end"),
            total_duration_seconds=data.get("total_duration_seconds"),
            errors=data.get("errors", []),
        )
