"""Shared dependencies — Singleton instances for stores and registries."""
from src.session.session_store import SessionStore
from src.session.env_registry import EnvRegistry

# Singletons (created once, shared across all requests)
_store: SessionStore = None
_env_registry: EnvRegistry = None


def get_store() -> SessionStore:
    global _store
    if _store is None:
        _store = SessionStore()
    return _store


def get_env_registry() -> EnvRegistry:
    global _env_registry
    if _env_registry is None:
        _env_registry = EnvRegistry()
    return _env_registry


def reset_stores():
    """Reset stores (used in testing)."""
    global _store, _env_registry
    _store = None
    _env_registry = None
