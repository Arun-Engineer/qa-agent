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
    Browser UI relies on session auth after login.

    Flow:
      1. No API_SECRET_KEY configured -> pass everything (dev mode)
      2. Public paths (/login, /health, /docs) -> always pass
      3. Static files -> always pass
      4. Non-/api/ paths (HTML pages like /dashboard, /agent-ui, /admin) -> always pass
      5. /api/* with valid browser session -> pass (dashboard JS calls)
      6. /api/* with valid X-API-Key header -> pass (external clients)
      7. /api/* with neither -> 401
    """

    async def dispatch(self, request: Request, call_next):
        path = request.url.path or ""

        expected_key = (os.getenv("API_SECRET_KEY") or "").strip()

        # 1. No key configured -> don't enforce (local/dev convenience)
        if not expected_key:
            return await call_next(request)

        # 2. Public paths
        if path in PUBLIC_PATHS:
            return await call_next(request)

        # 3. Static files
        if path.startswith("/static/"):
            return await call_next(request)

        # 4. Non-API paths (HTML pages) -> let them through, they have their
        #    own session checks via Depends(require_session) in route handlers
        if not path.startswith("/api/"):
            return await call_next(request)

        # --- From here, path starts with /api/ ---

        # 5. Browser session check: if the user is logged in via cookie,
        #    skip API key (this is how /dashboard -> /api/metrics works)
        try:
            if hasattr(request, "session") and request.session:
                session_user = (
                    request.session.get("user_id")
                    or request.session.get("account_id")
                )
                if session_user:
                    request.state.authenticated = True
                    return await call_next(request)
        except Exception:
            pass

        # Fallback: check raw cookie header (in case SessionMiddleware
        # hasn't decoded yet at this middleware layer)
        cookie = request.headers.get("cookie") or ""
        if "session" in cookie:
            return await call_next(request)

        # 6. API key check for programmatic clients
        api_key = (
            request.headers.get("X-API-Key")
            or request.query_params.get("api_key")
        )

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
