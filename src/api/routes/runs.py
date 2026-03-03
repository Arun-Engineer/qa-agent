"""Test Run Lifecycle API — Create, list, get, update runs."""
from fastapi import APIRouter, HTTPException, Query
from typing import Optional
from src.models.schemas import CreateRunRequest, RunResponse, RunListResponse
from src.api.dependencies import get_store

router = APIRouter()


@router.post("/", response_model=RunResponse, status_code=201)
async def create_run(req: CreateRunRequest):
    store = get_store()
    session = store.get_session(req.session_id)
    if not session:
        raise HTTPException(status_code=404, detail=f"Session {req.session_id} not found")

    if session.is_expired:
        raise HTTPException(status_code=400, detail="Session expired")

    if req.test_type in ("regression", "custom") and not session.can_write:
        raise HTTPException(
            status_code=403,
            detail=f"Write operations not allowed in {session.environment.value}"
        )

    run = store.create_run(
        session_id=req.session_id,
        test_type=req.test_type,
        target_url=req.target_url,
        description=req.description,
    )
    if not run:
        raise HTTPException(status_code=500, detail="Failed to create run")

    return RunResponse(**run)


@router.get("/", response_model=RunListResponse)
async def list_runs(
    session_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
):
    store = get_store()
    runs = store.list_runs(session_id=session_id, status=status)
    return RunListResponse(
        runs=[RunResponse(**r) for r in runs],
        total=len(runs),
    )


@router.get("/{run_id}", response_model=RunResponse)
async def get_run(run_id: str):
    store = get_store()
    run = store.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    return RunResponse(**run)


@router.patch("/{run_id}/status")
async def update_run_status(run_id: str, status: str = Query(...), results_summary: dict = None):
    store = get_store()
    valid_statuses = {"queued", "running", "completed", "failed", "cancelled"}
    if status not in valid_statuses:
        raise HTTPException(status_code=400, detail=f"Invalid status. Must be one of: {valid_statuses}")

    run = store.update_run_status(run_id, status, results_summary)
    if not run:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    return run
