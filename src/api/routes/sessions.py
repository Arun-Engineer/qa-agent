"""Session Management API — Create, list, get, cancel sessions."""
from fastapi import APIRouter, HTTPException, Query
from typing import Optional
from src.models.schemas import (
    CreateSessionRequest, SessionResponse, SessionListResponse, ErrorResponse
)
from src.api.dependencies import get_store, get_env_registry

router = APIRouter()


@router.post("/", response_model=SessionResponse, status_code=201)
async def create_session(req: CreateSessionRequest):
    """Create a new isolated test session.

    Environment rules are automatically applied:
    - SIT: full access, generate data freely
    - UAT: controlled access, use seeded data
    - PROD: read-only, every action needs approval
    """
    store = get_store()
    registry = get_env_registry()

    # Validate environment exists
    valid, msg = registry.validate_env(req.environment)
    if not valid:
        raise HTTPException(status_code=400, detail=msg)

    ctx = store.create_session(
        user_id=req.user_id,
        environment=req.environment,
        task=req.task,
        priority=req.priority,
        feature_branch=req.feature_branch,
    )

    return SessionResponse(
        session_id=ctx.session_id,
        user_id=ctx.user_id,
        environment=ctx.environment.value,
        task=ctx.task,
        access_mode=ctx.access_mode.value,
        priority=ctx.priority,
        can_write=ctx.can_write,
        created_at=ctx.created_at.isoformat(),
        expires_at=ctx.expires_at.isoformat() if ctx.expires_at else None,
        status=ctx.status.value,
    )


@router.get("/", response_model=SessionListResponse)
async def list_sessions(
    user_id: Optional[str] = Query(None),
    environment: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
):
    """List sessions with optional filters."""
    store = get_store()
    sessions = store.list_sessions(user_id=user_id, environment=environment, status=status)
    return SessionListResponse(
        sessions=[
            SessionResponse(
                session_id=s.session_id, user_id=s.user_id,
                environment=s.environment.value, task=s.task,
                access_mode=s.access_mode.value, priority=s.priority,
                can_write=s.can_write, created_at=s.created_at.isoformat(),
                expires_at=s.expires_at.isoformat() if s.expires_at else None,
                status=s.status.value,
            )
            for s in sessions
        ],
        total=len(sessions),
    )


@router.get("/{session_id}", response_model=SessionResponse)
async def get_session(session_id: str):
    """Get session details including access rules and expiry status."""
    store = get_store()
    ctx = store.get_session(session_id)
    if not ctx:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")

    return SessionResponse(
        session_id=ctx.session_id, user_id=ctx.user_id,
        environment=ctx.environment.value, task=ctx.task,
        access_mode=ctx.access_mode.value, priority=ctx.priority,
        can_write=ctx.can_write, created_at=ctx.created_at.isoformat(),
        expires_at=ctx.expires_at.isoformat() if ctx.expires_at else None,
        status="expired" if ctx.is_expired else ctx.status.value,
    )


@router.delete("/{session_id}")
async def cancel_session(session_id: str):
    """Cancel/stop an active session."""
    store = get_store()
    ctx = store.cancel_session(session_id)
    if not ctx:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    return {"session_id": session_id, "status": "cancelled"}


@router.post("/{session_id}/validate-action")
async def validate_action(session_id: str, action: str = Query(...)):
    """Check if an action is allowed in this session's environment.

    Actions: write, generate_data, destructive, read
    """
    store = get_store()
    ctx = store.get_session(session_id)
    if not ctx:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")

    allowed, reason = ctx.validate_action(action)
    return {"session_id": session_id, "action": action, "allowed": allowed, "reason": reason}
