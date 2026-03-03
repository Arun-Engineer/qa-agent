"""FastAPI application for AI QA Agent v5.

Phase 0+1+2: Foundation + Discovery Engine
  - Multi-tenant session management (SIT/UAT/PROD)
  - Environment-aware access control
  - Test run lifecycle
  - Zero-knowledge site discovery (Phase 2)
  - Audit logging on every request
  - API key authentication
  - Rate limiting
"""
import time
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
import structlog

load_dotenv()

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.dev.ConsoleRenderer(),
    ],
)

logger = structlog.get_logger()
START_TIME = time.time()


def create_app() -> FastAPI:
    app = FastAPI(
        title="AI QA Agent",
        version="5.0.0-phase3",
        description="Autonomous QA Platform — Phase 0+1+2: Foundation + Discovery",
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # Middleware (order matters: last added = first executed)
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

    from src.api.middleware.audit import AuditMiddleware
    from src.api.middleware.rate_limit import RateLimitMiddleware
    from src.api.middleware.auth import AuthMiddleware

    app.add_middleware(AuditMiddleware)
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(AuthMiddleware)

    # Phase 1 routes
    from src.api.routes import sessions, runs, environments
    app.include_router(sessions.router, prefix="/api/v1/sessions", tags=["Sessions"])
    app.include_router(runs.router, prefix="/api/v1/runs", tags=["Runs"])
    app.include_router(environments.router, prefix="/api/v1/environments", tags=["Environments"])

    # Phase 2 routes
    from src.api.routes import discovery
    app.include_router(discovery.router, prefix="/api/v1/discovery", tags=["Discovery"])

    # Health endpoint
    @app.get("/health", tags=["Health"])
    async def health():
        from src.api.dependencies import get_store, get_env_registry
        store = get_store()
        registry = get_env_registry()
        return {
            "status": "ok",
            "version": "5.0.0-phase3",
            "uptime_seconds": round(time.time() - START_TIME, 2),
            "active_sessions": store.get_active_count(),
            "total_runs": store.get_total_runs(),
            "environments": registry.list_all(),
        }

    logger.info("app_started", version="5.0.0-phase3")
    return app


app = create_app()
