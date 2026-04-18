"""agent/auth/registry.py — Plugin registry."""
from __future__ import annotations

from typing import Optional

from agent.auth.base import AuthPlugin


_PLUGINS: dict[str, AuthPlugin] = {}


def register(plugin: AuthPlugin) -> None:
    _PLUGINS[plugin.name] = plugin


def get(name: str) -> Optional[AuthPlugin]:
    return _PLUGINS.get(name)


def list_plugins() -> list[str]:
    return sorted(_PLUGINS.keys())


def best_for(ctx: dict) -> Optional[AuthPlugin]:
    """Ask each plugin how confident it is; return the winner (or None)."""
    best: Optional[AuthPlugin] = None
    best_score = 0.0
    for p in _PLUGINS.values():
        try:
            score = float(p.detect(ctx) or 0)
        except Exception:
            score = 0.0
        if score > best_score:
            best, best_score = p, score
    return best
