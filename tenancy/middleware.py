# tenancy/middleware.py
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response, PlainTextResponse
from sqlalchemy.orm import Session

from auth.db import SessionLocal
from tenancy.resolve import resolve_tenant, is_apex_host, split_host


class TenantContextMiddleware(BaseHTTPMiddleware):
    """
    Attaches:
      request.state.tenant (Tenant|None)
      request.state.tenant_id (str|None)
      request.state.tenant_slug (str|None)
      request.state.is_apex (bool)
    """

    async def dispatch(self, request: Request, call_next):
        host = request.headers.get("host", "")
        hostname, _ = split_host(host)

        db: Session = SessionLocal()
        try:
            tenant = resolve_tenant(db, host)
        finally:
            db.close()

        request.state.tenant = tenant
        request.state.tenant_id = getattr(tenant, "id", None)
        request.state.tenant_slug = getattr(tenant, "slug", None)
        request.state.is_apex = is_apex_host(hostname)

        # If it looks like a tenant host but tenant not found -> 404
        # (prevents leaking apex pages on random subdomains)
        if (not request.state.is_apex) and tenant is None:
            return PlainTextResponse("Tenant not found", status_code=404)

        return await call_next(request)