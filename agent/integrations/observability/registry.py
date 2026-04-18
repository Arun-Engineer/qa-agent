"""agent/integrations/observability/registry.py — Pick the right adapter.

Each vendor adapter registers itself on import. ``detect(base_url, sample_text)``
returns the highest-scoring adapter, or a generic fallback so the probe can
run even on unknown platforms (it just won't know vendor-specific field
names and may report lower-confidence findings).

Add a new vendor by writing a new adapter module under
``agent/integrations/observability/adapters/`` and calling ``register()``
at import time.
"""
from __future__ import annotations

from typing import Optional
from urllib.parse import urlparse

from agent.integrations.observability.base import BaseAdapter, PlatformAdapter


_REGISTRY: dict[str, PlatformAdapter] = {}


def register(adapter: PlatformAdapter) -> None:
    _REGISTRY[adapter.name] = adapter


def get(name: str) -> Optional[PlatformAdapter]:
    return _REGISTRY.get(name)


def list_adapters() -> list[dict]:
    return [{"name": a.name, "display_name": a.display_name,
             "signals": a.signal_keywords[:6],
             "urls": a.url_patterns}
            for a in _REGISTRY.values()]


def detect(base_url: str, sample_text: str = "") -> PlatformAdapter:
    """Pick the best-matching registered adapter, or a generic fallback."""
    host = urlparse(base_url).netloc
    best: tuple[float, Optional[PlatformAdapter]] = (0.0, None)
    for a in _REGISTRY.values():
        # score_match lives on BaseAdapter; duck-type it.
        if hasattr(a, "score_match"):
            s = a.score_match(sample_text, host)
            if s > best[0]:
                best = (s, a)
    if best[1] and best[0] >= 0.2:
        return best[1]
    # Fallback: generic adapter — known paths + neutral payload shape.
    return _REGISTRY.get("generic") or BaseAdapter()


# Trigger adapter registration on package import.
def _autoload():
    from agent.integrations.observability.adapters import (  # noqa: F401
        puvi, langsmith, langfuse, arize, generic,
    )


_autoload()
