"""API Authentication Middleware — API key validation."""
import os
import structlog
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

logger = structlog.get_logger()

PUBLIC_PATHS = {
    "/",
    "/login",
    "/health",
    "/docs",
    "/openapi.json",
    "/redoc",
}

class AuthMiddleware(BaseHTTPMiddleware):
    """
    API key validation for non-browser clients.
    Browser UI should rely on Session auth after login.
    """

    async def dispatch(self, request: Request, call_next):
        path = request.url.path or ""

        expected_key = (os.getenv("API_SECRET_KEY") or "").strip()

        # If no key configured, don't enforce (local/dev convenience)
        if not expected_key:
            return await call_next(request)

        # Always allow public paths
        if path in PUBLIC_PATHS:
            return await call_next(request)

        # ✅ OPTIONAL: enforce only for /api/v1/* (recommended)
        # If you WANT to enforce for everything under /api/, change this back to "/api/".
        if not path.startswith("/api/v1/") and not path.startswith("/api/"):
            return await call_next(request)

        # ✅ If browser session exists (logged-in), skip API key checks
        # This makes /dashboard -> /api/metrics work.
        try:
            if hasattr(request, "session") and request.session:
                request.state.authenticated = True
                return await call_next(request)
        except Exception:
            # if sessions not configured properly, fall through to API-key auth
            pass

        # Enforce API key for non-session clients
        api_key = request.headers.get("X-API-Key") or request.query_params.get("api_key")

        if not api_key:
            logger.warning(
                "auth_missing_key",
                path=path,
                client=getattr(request.client, "host", None),
            )
            return JSONResponse(
                status_code=401,
                content={"detail": "Missing API key. Include X-API-Key header."},
            )

        if api_key != expected_key:
            logger.warning(
                "auth_invalid_key",
                path=path,
                client=getattr(request.client, "host", None),
            )
            return JSONResponse(
                status_code=403,
                content={"detail": "Invalid API key."},
            )

        request.state.authenticated = True
        return await call_next(request)