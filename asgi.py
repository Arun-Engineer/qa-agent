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
import tenancy.content_models  # noqa (if you already added this earlier)

from auth.routes import router as auth_router
from tenancy.routes_apex import router as apex_router
from tenancy.admin_api import router as admin_api_router
from tenancy.tenant_agent_api import router as agent_api_router

BASE_DOMAIN = os.getenv("BASE_DOMAIN", "lvh.me")
SESSION_SECRET = os.getenv("SESSION_SECRET", "dev-only-change-me")
DEFAULT_TENANT = os.getenv("DEFAULT_TENANT", "local")

apex_app = FastAPI(title="QA Agent - Apex")
tenant_app = FastAPI(title="QA Agent - Tenant")

tenant_app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET)

Base.metadata.create_all(bind=engine)

@tenant_app.middleware("http")
async def ensure_tenant_context(request: Request, call_next):
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

apex_app.include_router(apex_router)
tenant_app.include_router(auth_router)

# ✅ Phase 2 admin router first
tenant_app.include_router(admin_api_router)

tenant_app.include_router(agent_api_router)

app = Starlette(
    routes=[
        Host("localhost", app=tenant_app),
        Host("127.0.0.1", app=tenant_app),
        Host(BASE_DOMAIN, app=apex_app),
        Host(f"{{tenant}}.{BASE_DOMAIN}", app=tenant_app),
        Host("{any_host:path}", app=tenant_app),
    ]
)