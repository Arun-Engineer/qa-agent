"""Pydantic schemas for API request/response models."""
from pydantic import BaseModel, Field
from typing import Optional, Literal
from datetime import datetime


# --- Session Schemas ---
class CreateSessionRequest(BaseModel):
    user_id: str = Field(..., min_length=1, description="Unique user identifier")
    environment: Literal["sit", "uat", "prod"] = Field("sit", description="Target environment")
    task: str = Field(..., min_length=1, description="What this session will test")
    feature_branch: Optional[str] = Field(None, description="Git branch being tested")
    priority: Literal["critical", "high", "normal", "low"] = Field("normal")


class SessionResponse(BaseModel):
    session_id: str
    user_id: str
    environment: str
    task: str
    access_mode: str
    priority: str
    can_write: bool
    created_at: str
    expires_at: Optional[str] = None
    status: str = "active"


class SessionListResponse(BaseModel):
    sessions: list[SessionResponse]
    total: int


# --- Run Schemas ---
class CreateRunRequest(BaseModel):
    session_id: str = Field(..., description="Session to run tests in")
    target_url: Optional[str] = Field(None, description="URL to test (for discovery)")
    test_type: Literal["discovery", "regression", "smoke", "custom"] = Field("smoke")
    description: Optional[str] = None


class RunResponse(BaseModel):
    run_id: str
    session_id: str
    status: str  # queued | running | completed | failed
    test_type: str
    target_url: Optional[str] = None
    created_at: str
    completed_at: Optional[str] = None
    results_summary: Optional[dict] = None


class RunListResponse(BaseModel):
    runs: list[RunResponse]
    total: int


# --- Health ---
class HealthResponse(BaseModel):
    status: str
    version: str
    uptime_seconds: float
    active_sessions: int
    total_runs: int
    environment_count: int


# --- Error ---
class ErrorResponse(BaseModel):
    error: str
    detail: str
    status_code: int
