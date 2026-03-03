"""Root entry point — delegates to src/api/main.py.

Usage:
  uvicorn main:app --reload          (dev)
  uvicorn src.api.main:app --reload  (also works)
  uvicorn asgi:app --reload          (production, full multi-tenant)
"""
from src.api.main import app  # noqa: F401
