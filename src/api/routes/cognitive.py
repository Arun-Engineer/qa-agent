"""
src/api/routes/cognitive.py — REST API for Phase 3 Cognitive Agents.

Endpoints:
  POST /api/v1/cognitive/strategy     → generate test strategy from site model
  POST /api/v1/cognitive/generate     → generate test code from strategy
  POST /api/v1/cognitive/triage       → classify test failures
  POST /api/v1/cognitive/heal         → self-heal broken tests
  POST /api/v1/cognitive/pipeline     → full pipeline (strategy→generate→execute→triage)
"""
from __future__ import annotations

import json
from pathlib import Path
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from typing import Optional

router = APIRouter()


class StrategyRequest(BaseModel):
    site_model_path: Optional[str] = None
    site_model: Optional[dict] = None
    spec_text: Optional[str] = None
    target_url: Optional[str] = None
    environment: str = "SIT"
    provider: Optional[str] = None
    model: Optional[str] = None


class GenerateRequest(BaseModel):
    strategy: dict
    site_model: Optional[dict] = None
    target_url: Optional[str] = None
    spec_text: Optional[str] = None
    environment: str = "SIT"
    provider: Optional[str] = None
    model: Optional[str] = None


class TriageRequest(BaseModel):
    failures: list[dict]
    target_url: Optional[str] = None
    environment: str = "SIT"
    provider: Optional[str] = None
    model: Optional[str] = None


class HealRequest(BaseModel):
    failed_test: dict
    dom_snapshot: str = ""
    target_url: Optional[str] = None
    provider: Optional[str] = None
    model: Optional[str] = None


class PipelineRequest(BaseModel):
    site_model_path: Optional[str] = None
    site_model: Optional[dict] = None
    spec_text: Optional[str] = None
    target_url: Optional[str] = None
    environment: str = "SIT"
    execute_tests: bool = False
    auto_heal: bool = False
    provider: Optional[str] = None
    model: Optional[str] = None


def _load_site_model(path: str | None, inline: dict | None) -> dict | None:
    if inline:
        return inline
    if path:
        p = Path(path)
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    return None


def _get_tenant_id(request: Request) -> str:
    return getattr(request.state, "tenant_id", None) or "default"


@router.post("/strategy")
async def create_strategy(req: StrategyRequest, request: Request):
    from src.cognitive.agents.base_agent import AgentContext
    from src.cognitive.agents.test_strategy import TestStrategyAgent

    site_model = _load_site_model(req.site_model_path, req.site_model)
    ctx = AgentContext(
        tenant_id=_get_tenant_id(request),
        site_model=site_model,
        spec_text=req.spec_text,
        target_url=req.target_url,
        environment=req.environment,
        provider=req.provider,
        model=req.model,
    )

    agent = TestStrategyAgent()
    result = await agent.execute(ctx)

    if result.status != "ok":
        raise HTTPException(status_code=500, detail=result.error)
    return {"status": "ok", "strategy": result.data,
            "llm_calls": result.llm_calls, "tokens": result.tokens_used,
            "duration_ms": result.duration_ms}


@router.post("/generate")
async def generate_tests(req: GenerateRequest, request: Request):
    from src.cognitive.agents.base_agent import AgentContext
    from src.cognitive.agents.test_generator import TestGeneratorAgent

    ctx = AgentContext(
        tenant_id=_get_tenant_id(request),
        site_model=req.site_model,
        spec_text=req.spec_text,
        target_url=req.target_url,
        environment=req.environment,
        provider=req.provider,
        model=req.model,
    )

    agent = TestGeneratorAgent()
    results = await agent.generate_for_strategy(ctx, req.strategy)

    tests = []
    total_calls = 0
    total_tokens = 0
    for r in results:
        total_calls += r.llm_calls
        total_tokens += r.tokens_used
        if r.data:
            tests.append(r.data)

    return {"status": "ok", "tests": tests, "count": len(tests),
            "llm_calls": total_calls, "tokens": total_tokens}


@router.post("/triage")
async def triage_failures(req: TriageRequest, request: Request):
    from src.cognitive.agents.base_agent import AgentContext
    from src.cognitive.agents.failure_triage import FailureTriageAgent

    ctx = AgentContext(
        tenant_id=_get_tenant_id(request),
        target_url=req.target_url,
        environment=req.environment,
        provider=req.provider,
        model=req.model,
        extra={"failures": req.failures},
    )

    agent = FailureTriageAgent()
    result = await agent.execute(ctx)

    if result.status != "ok":
        raise HTTPException(status_code=500, detail=result.error)
    return {"status": "ok", "triage": result.data,
            "llm_calls": result.llm_calls, "tokens": result.tokens_used}


@router.post("/heal")
async def heal_test(req: HealRequest, request: Request):
    from src.cognitive.agents.base_agent import AgentContext
    from src.cognitive.agents.self_healer import SelfHealerAgent

    ctx = AgentContext(
        tenant_id=_get_tenant_id(request),
        target_url=req.target_url,
        provider=req.provider,
        model=req.model,
        extra={"failed_test": req.failed_test, "dom_snapshot": req.dom_snapshot},
    )

    agent = SelfHealerAgent()
    result = await agent.execute(ctx)

    if result.status != "ok":
        raise HTTPException(status_code=500, detail=result.error)
    return {"status": "ok", "healed": result.data,
            "llm_calls": result.llm_calls, "tokens": result.tokens_used}


@router.post("/pipeline")
async def run_pipeline(req: PipelineRequest, request: Request):
    from src.cognitive.agents.base_agent import AgentContext
    from src.cognitive.orchestrator import CognitiveOrchestrator

    site_model = _load_site_model(req.site_model_path, req.site_model)
    ctx = AgentContext(
        tenant_id=_get_tenant_id(request),
        site_model=site_model,
        spec_text=req.spec_text,
        target_url=req.target_url,
        environment=req.environment,
        provider=req.provider,
        model=req.model,
    )

    orchestrator = CognitiveOrchestrator(provider=req.provider, model=req.model)
    result = await orchestrator.run_full_pipeline(
        ctx, execute_tests=req.execute_tests, auto_heal=req.auto_heal)

    return result.to_dict()
