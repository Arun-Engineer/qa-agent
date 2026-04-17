"""src/api/routes/rag.py — REST API for the RAG / guardrails / observability pack.

Endpoints (all under /api/v1/rag/* once mounted):
  POST /chain/run           → execute a prompt chain (ui_test | api_test | spec_review)
  GET  /chain/list          → list registered chains
  POST /guard/check         → run InputGuard on arbitrary text
  POST /guard/check-output  → run OutputFilter on arbitrary text
  POST /eval/offline        → run offline eval against the golden dataset
  GET  /cost/summary        → cost tracker summary (per tenant / workflow / window)
  GET  /traces              → recent traces
  GET  /health              → health of online-quality monitor
  POST /feedback            → record user feedback

These endpoints do NOT replace anything. They sit alongside the legacy routes.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from src.rag.integration import (
    get_chain_engine, get_prompt_registry,
    get_input_guard, get_output_filter,
    get_cost_tracker, get_tracer, get_feedback_collector, get_online_monitor,
)
from src.rag.agents.prompt_chain_engine import CHAIN_REGISTRY
from monitoring.feedback import FeedbackEntry


router = APIRouter()


# ── Models ────────────────────────────────────────────────────────────────────

class ChainRunRequest(BaseModel):
    chain_name: str = Field(..., description="ui_test | api_test | spec_review")
    context: dict[str, Any] = Field(default_factory=dict)
    tenant_id: Optional[str] = None


class GuardCheckRequest(BaseModel):
    text: str


class OfflineEvalRequest(BaseModel):
    category: Optional[str] = None
    tags: Optional[list[str]] = None


class FeedbackRequest(BaseModel):
    run_id: str
    workflow: str
    rating: int = Field(..., ge=1, le=5)
    category: str = ""
    comment: str = ""
    tenant_id: str = ""


# ── Helpers ───────────────────────────────────────────────────────────────────

def _tenant_id(request: Request, explicit: Optional[str]) -> str:
    if explicit:
        return explicit
    return getattr(request.state, "tenant_slug", "") or getattr(request.state, "tenant_id", "") or ""


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/chain/list")
async def list_chains() -> dict:
    return {
        "chains": list(CHAIN_REGISTRY.keys()),
        "templates": get_prompt_registry().list_templates(),
    }


@router.post("/chain/run")
async def run_chain(req: ChainRunRequest, request: Request) -> dict:
    if req.chain_name not in CHAIN_REGISTRY:
        raise HTTPException(status_code=400, detail=f"unknown chain: {req.chain_name}")
    engine = get_chain_engine()
    tid = _tenant_id(request, req.tenant_id)
    try:
        result = await engine.execute(req.chain_name, initial_context=req.context, tenant_id=tid)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"chain execution failed: {e}") from e
    return {
        "chain_id": result.chain_id,
        "chain_name": result.chain_name,
        "status": result.status,
        "total_duration_ms": result.total_duration_ms,
        "total_llm_calls": result.total_llm_calls,
        "total_tokens": result.total_tokens,
        "steps": [
            {"name": s.step_name, "status": s.status.value, "duration_ms": s.duration_ms,
             "llm_calls": s.llm_calls, "tokens": s.tokens_used, "retries": s.retries,
             "error": s.error, "output": s.output}
            for s in result.steps
        ],
        "final_output": result.final_output,
    }


@router.post("/guard/check")
async def check_input(req: GuardCheckRequest) -> dict:
    r = get_input_guard().check(req.text)
    return {
        "is_safe": r.is_safe,
        "threat_level": r.threat_level.value,
        "threats": r.threats_detected,
        "sanitized": r.sanitized_input,
    }


@router.post("/guard/check-output")
async def check_output(req: GuardCheckRequest) -> dict:
    r = get_output_filter().check(req.text)
    return {"is_safe": r.is_safe, "issues": r.issues, "confidence": r.confidence}


@router.post("/eval/offline")
async def run_offline_eval(req: OfflineEvalRequest) -> dict:
    # Local import so the golden-dataset file is only created on first use.
    from src.evaluation.offline_eval import OfflineEvaluator
    report = OfflineEvaluator().run_eval(category=req.category, tags=req.tags)
    return {
        "run_id": report.run_id, "timestamp": report.timestamp,
        "category": report.category, "total_cases": report.total_cases,
        "passed": report.passed, "failed": report.failed,
        "pass_rate": report.pass_rate, "avg_score": report.avg_score,
        "duration_ms": report.duration_ms,
        "scores": [{"case_id": s.case_id, "passed": s.passed, "score": s.score, "details": s.details}
                   for s in report.scores],
    }


@router.get("/cost/summary")
async def cost_summary(
    request: Request,
    tenant_id: Optional[str] = None,
    workflow: Optional[str] = None,
    hours: int = 24,
) -> dict:
    tid = _tenant_id(request, tenant_id) or None
    return get_cost_tracker().get_summary(tenant_id=tid, workflow=workflow, last_n_hours=hours)


@router.get("/traces")
async def list_traces(workflow: Optional[str] = None, limit: int = 20) -> dict:
    return {"traces": get_tracer().get_recent_traces(workflow=workflow, limit=limit)}


@router.get("/health")
async def quality_health(workflow: Optional[str] = None) -> dict:
    return get_online_monitor().get_health(workflow=workflow)


@router.post("/feedback")
async def record_feedback(req: FeedbackRequest, request: Request) -> dict:
    tid = _tenant_id(request, req.tenant_id)
    get_feedback_collector().record(FeedbackEntry(
        run_id=req.run_id, workflow=req.workflow, tenant_id=tid,
        rating=req.rating, category=req.category, comment=req.comment,
    ))
    stats = get_feedback_collector().get_stats(workflow=req.workflow)
    return {"recorded": True, "stats": stats}
