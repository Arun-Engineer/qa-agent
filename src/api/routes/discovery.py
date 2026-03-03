"""Discovery API — Trigger and retrieve site discovery results.

Phase 2: Zero-Knowledge Discovery Engine.
Integrates with existing session management and RBAC.
"""
from __future__ import annotations

import asyncio
import structlog
from fastapi import APIRouter, HTTPException, Query, Depends
from pydantic import BaseModel, Field
from typing import Optional, Literal

from src.api.dependencies import get_store
from src.discovery.engine import DiscoveryEngine
from src.discovery.site_model import SiteModel

logger = structlog.get_logger()
router = APIRouter()


# ─── Request / Response Schemas ───────────────────────────────

class StartDiscoveryRequest(BaseModel):
    session_id: str = Field(..., description="Active session to bind this discovery to")
    target_url: str = Field(..., description="Base URL to discover (e.g. https://example.com)")
    max_pages: int = Field(50, ge=1, le=500, description="Max pages to crawl")
    max_depth: int = Field(3, ge=1, le=10, description="Max link-follow depth")
    strategy: Literal["bfs", "dfs"] = Field("bfs", description="Crawl strategy")
    login_url: Optional[str] = None
    login_username: Optional[str] = None
    login_password: Optional[str] = None
    screenshot: bool = Field(True, description="Capture screenshots per page")


class DiscoveryStatusResponse(BaseModel):
    run_id: str
    session_id: str
    target_url: str
    status: str
    pages_discovered: int = 0
    components_found: int = 0
    api_endpoints_found: int = 0
    site_model_path: Optional[str] = None


# ─── In-memory result store (Phase 4 → PostgreSQL) ───────────

_discovery_results: dict[str, dict] = {}


# ─── Endpoints ────────────────────────────────────────────────

@router.post("/", response_model=DiscoveryStatusResponse, status_code=201)
async def start_discovery(req: StartDiscoveryRequest):
    """Launch a discovery crawl for a target URL within an existing session."""
    store = get_store()
    session = store.get_session(req.session_id)
    if not session:
        raise HTTPException(status_code=404, detail=f"Session {req.session_id} not found")
    if session.is_expired:
        raise HTTPException(status_code=400, detail="Session expired")

    # Create a run record linked to this session
    run = store.create_run(
        session_id=req.session_id,
        test_type="discovery",
        target_url=req.target_url,
        description=f"Discovery crawl: {req.target_url} (max {req.max_pages} pages, {req.strategy})",
    )
    if not run:
        raise HTTPException(status_code=500, detail="Failed to create discovery run")

    run_id = run["run_id"]
    store.update_run_status(run_id, "running")

    # Build login config if provided
    login_config = None
    if req.login_url and req.login_username and req.login_password:
        login_config = {
            "login_url": req.login_url,
            "username": req.login_username,
            "password": req.login_password,
        }

    # Execute discovery (run in threadpool since Playwright is sync-heavy)
    try:
        engine = DiscoveryEngine(
            base_url=req.target_url,
            max_pages=req.max_pages,
            max_depth=req.max_depth,
            strategy=req.strategy,
            login_config=login_config,
            screenshot=req.screenshot,
        )
        site_model: SiteModel = await asyncio.to_thread(engine.run)

        model_path = site_model.save()

        _discovery_results[run_id] = {
            "site_model": site_model,
            "model_path": model_path,
        }

        store.update_run_status(run_id, "completed", results_summary={
            "pages_discovered": len(site_model.pages),
            "components_found": sum(len(p.components) for p in site_model.pages),
            "api_endpoints_found": len(site_model.api_endpoints),
            "model_path": model_path,
        })

        logger.info("discovery_completed",
                     run_id=run_id, pages=len(site_model.pages),
                     apis=len(site_model.api_endpoints))

        return DiscoveryStatusResponse(
            run_id=run_id,
            session_id=req.session_id,
            target_url=req.target_url,
            status="completed",
            pages_discovered=len(site_model.pages),
            components_found=sum(len(p.components) for p in site_model.pages),
            api_endpoints_found=len(site_model.api_endpoints),
            site_model_path=model_path,
        )

    except Exception as e:
        logger.error("discovery_failed", run_id=run_id, error=str(e))
        store.update_run_status(run_id, "failed", results_summary={"error": str(e)})
        raise HTTPException(status_code=500, detail=f"Discovery failed: {e}")


@router.get("/{run_id}", response_model=DiscoveryStatusResponse)
async def get_discovery_status(run_id: str):
    """Get the current status of a discovery run."""
    store = get_store()
    run = store.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    if run.get("test_type") != "discovery":
        raise HTTPException(status_code=400, detail="Not a discovery run")

    summary = run.get("results_summary") or {}
    return DiscoveryStatusResponse(
        run_id=run_id,
        session_id=run["session_id"],
        target_url=run.get("target_url", ""),
        status=run["status"],
        pages_discovered=summary.get("pages_discovered", 0),
        components_found=summary.get("components_found", 0),
        api_endpoints_found=summary.get("api_endpoints_found", 0),
        site_model_path=summary.get("model_path"),
    )


@router.get("/{run_id}/site-model")
async def get_site_model(run_id: str):
    """Return the full site model JSON for a completed discovery run."""
    result = _discovery_results.get(run_id)
    if not result:
        store = get_store()
        run = store.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="Run not found")

        model_path = (run.get("results_summary") or {}).get("model_path")
        if model_path:
            try:
                model = SiteModel.load(model_path)
                return model.to_dict()
            except Exception:
                raise HTTPException(status_code=404, detail="Site model file not found")
        raise HTTPException(status_code=404, detail="Discovery not completed or model not available")

    return result["site_model"].to_dict()


@router.get("/{run_id}/pages")
async def list_discovered_pages(run_id: str):
    """List all discovered pages with classification."""
    result = _discovery_results.get(run_id)
    if not result:
        raise HTTPException(status_code=404, detail="Discovery result not in memory")

    model: SiteModel = result["site_model"]
    return {
        "run_id": run_id,
        "total": len(model.pages),
        "pages": [
            {
                "url": p.url,
                "title": p.title,
                "page_type": p.page_type,
                "components_count": len(p.components),
                "depth": p.depth,
                "status_code": p.status_code,
            }
            for p in model.pages
        ],
    }


@router.get("/{run_id}/api-surface")
async def list_api_surface(run_id: str):
    """List all captured API endpoints."""
    result = _discovery_results.get(run_id)
    if not result:
        raise HTTPException(status_code=404, detail="Discovery result not in memory")

    model: SiteModel = result["site_model"]
    return {
        "run_id": run_id,
        "total": len(model.api_endpoints),
        "endpoints": [ep.to_dict() for ep in model.api_endpoints],
    }
