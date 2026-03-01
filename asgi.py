# asgi.py
import os
from pathlib import Path

from fastapi import FastAPI, Request, HTTPException
from fastapi.exception_handlers import http_exception_handler as fastapi_http_exception_handler
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.applications import Starlette
from starlette.middleware.sessions import SessionMiddleware
from starlette.routing import Host
from sqlalchemy import select

from auth.db import Base, engine, SessionLocal

# IMPORTANT: import tenancy models before create_all
from tenancy.models import Tenant  # noqa
import tenancy.content_models  # noqa

from auth.routes import router as auth_router
from tenancy.routes_apex import router as apex_router
from tenancy.admin_api import router as admin_api_router
from tenancy.tenant_agent_api import router as agent_api_router

# ✅ Phase 0+1 core API pieces (from src/api/main.py)
from src.api.middleware.audit import AuditMiddleware
from src.api.middleware.rate_limit import RateLimitMiddleware
from src.api.middleware.auth import AuthMiddleware
from src.api.routes import sessions, runs, environments
from src.api.dependencies import get_store, get_env_registry

BASE_DOMAIN = os.getenv("BASE_DOMAIN", "lvh.me")
SESSION_SECRET = os.getenv("SESSION_SECRET", "dev-only-change-me")
DEFAULT_TENANT = os.getenv("DEFAULT_TENANT", "local")

apex_app = FastAPI(title="QA Agent - Apex")
tenant_app = FastAPI(title="QA Agent - Tenant")

# Session cookies for login
tenant_app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET)

# 🔗 Add Phase 0+1 middlewares on tenant_app as well
tenant_app.add_middleware(AuditMiddleware)
tenant_app.add_middleware(RateLimitMiddleware)
tenant_app.add_middleware(AuthMiddleware)

# Create all DB tables (including tenancy models)
Base.metadata.create_all(bind=engine)


@tenant_app.middleware("http")
async def ensure_tenant_context(request: Request, call_next):
    """
    Resolve tenant from host and attach to request.state.
    """
    host_params = request.scope.get("path_params") or {}
    slug = host_params.get("tenant") or DEFAULT_TENANT

    db = SessionLocal()
    try:
        t = db.execute(select(Tenant).where(Tenant.slug == slug)).scalar_one_or_none()
        if not t:
            t = Tenant(slug=slug, name=slug.upper(), is_active=True)
            db.add(t)
            db.commit()
            db.refresh(t)

        request.state.tenant = t
        request.state.tenant_id = t.id
        request.state.tenant_slug = t.slug
    finally:
        db.close()

    return await call_next(request)


@tenant_app.exception_handler(HTTPException)
async def tenant_http_exception_handler(request: Request, exc: HTTPException):
    """
    For HTML requests, redirect unauthorized users to /login.
    For API clients (JSON), return normal FastAPI error.
    """
    accept = (request.headers.get("accept") or "").lower()
    if exc.status_code in (401, 403) and "text/html" in accept:
        nxt = request.url.path
        if request.url.query:
            nxt += f"?{request.url.query}"
        return RedirectResponse(url=f"/login?next={nxt}", status_code=303)
    return await fastapi_http_exception_handler(request, exc)


BASE_DIR = Path(__file__).resolve().parent
UI_DIR = BASE_DIR / "ui"
if UI_DIR.exists():
    tenant_app.mount("/static", StaticFiles(directory=str(UI_DIR)), name="static")

# Apex routes (top-level, non-tenant)
apex_app.include_router(apex_router)

# Tenant-level routes
tenant_app.include_router(auth_router)

# ✅ Phase 2 admin router first
tenant_app.include_router(admin_api_router)

# Agent APIs (your existing QA agent endpoints)
tenant_app.include_router(agent_api_router)

# ✅ Phase 0+1 JSON APIs under /api/v1/*
tenant_app.include_router(sessions.router, prefix="/api/v1/sessions", tags=["Sessions"])
tenant_app.include_router(runs.router, prefix="/api/v1/runs", tags=["Runs"])
tenant_app.include_router(environments.router, prefix="/api/v1/environments", tags=["Environments"])


# Optional: a health endpoint on the tenant app using the same store/registry
@tenant_app.get("/health", tags=["Health"])
async def health():
    store = get_store()
    registry = get_env_registry()
    return {
        "status": "ok",
        "version": "5.0.0-phase1",
        "active_sessions": store.get_active_count(),
        "total_runs": store.get_total_runs(),
        "environments": registry.list_all(),
    }


# Final ASGI app with host-based routing
app = Starlette(
    routes=[
        Host("localhost", app=tenant_app),
        Host("127.0.0.1", app=tenant_app),
        Host(BASE_DOMAIN, app=apex_app),
        Host(f"{'{tenant}'}.{BASE_DOMAIN}", app=tenant_app),
        Host("{any_host:path}", app=tenant_app),
    ]
)