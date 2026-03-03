"""Audit Trail Middleware — Logs every API request with full context."""
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
import time
import structlog

logger = structlog.get_logger()


class AuditMiddleware(BaseHTTPMiddleware):
    """Logs every request: who, what, when, how long, result."""

    async def dispatch(self, request: Request, call_next):
        start = time.time()
        method = request.method
        path = request.url.path
        client = request.client.host

        response = await call_next(request)

        duration_ms = round((time.time() - start) * 1000, 2)
        status = response.status_code

        log_fn = logger.info if status < 400 else logger.warning
        log_fn(
            "api_request",
            method=method, path=path, status=status,
            duration_ms=duration_ms, client_ip=client,
        )
        response.headers["X-Response-Time-Ms"] = str(duration_ms)
        return response
