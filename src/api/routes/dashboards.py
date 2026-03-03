"""
Phase 6B - Dashboard API Routes
REST endpoints for dashboard generation, listing, and stakeholder presets.
"""

import logging
import os
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dashboards", tags=["dashboards"])


# -- Request / Response Models -----------------------------------------------

class DashboardRequest(BaseModel):
    run_id: str
    preset: str = Field(default="qa_lead", description="Stakeholder preset: executive, qa_lead, developer, release, product")
    theme: str = Field(default="light", description="light or dark")
    title: Optional[str] = None

class DashboardResponse(BaseModel):
    dashboard_id: str
    preset: str
    file_path: str
    url: str
    chart_count: int
    generated_at: str

class GateCheckRequest(BaseModel):
    run_id: str
    profile: str = Field(default="standard", description="strict, standard, or relaxed")

class GateCheckResponse(BaseModel):
    verdict: str
    score: float
    confidence: float
    blocking_count: int
    warning_count: int
    ci_exit_code: int
    rules: List[Dict[str, Any]]


# -- Endpoints ---------------------------------------------------------------

@router.post("/generate", response_model=DashboardResponse)
async def generate_dashboard(req: DashboardRequest):
    """Generate a dashboard for a specific test run and stakeholder preset."""
    try:
        from src.reporting.dashboard_generator import (
            DashboardGenerator, DashboardConfig, StakeholderPreset,
        )

        preset_map = {
            "executive": StakeholderPreset.EXECUTIVE,
            "qa_lead": StakeholderPreset.QA_LEAD,
            "developer": StakeholderPreset.DEVELOPER,
            "release": StakeholderPreset.RELEASE_MANAGER,
            "product": StakeholderPreset.PRODUCT_OWNER,
        }
        preset = preset_map.get(req.preset)
        if not preset:
            raise HTTPException(400, f"Unknown preset: {req.preset}. Use: {list(preset_map.keys())}")

        # Load run data (from DB or file)
        run_data = await _load_run_data(req.run_id)

        config = DashboardConfig(
            title=req.title or f"QA Dashboard - {req.preset.replace('_', ' ').title()}",
            preset=preset,
            theme=req.theme,
        )

        gen = DashboardGenerator(output_dir="/app/artifacts/dashboards")
        filepath = gen.generate(run_data, config)
        filename = os.path.basename(filepath)

        return DashboardResponse(
            dashboard_id=filename.replace(".html", ""),
            preset=req.preset,
            file_path=filepath,
            url=f"/artifacts/dashboards/{filename}",
            chart_count=len(config.charts),
            generated_at=run_data.get("completed_at", ""),
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Dashboard generation failed: %s", e)
        raise HTTPException(500, f"Dashboard generation failed: {str(e)}")


@router.post("/generate-all", response_model=List[DashboardResponse])
async def generate_all_presets(run_id: str):
    """Generate dashboards for all stakeholder presets."""
    try:
        from src.reporting.dashboard_generator import DashboardGenerator

        run_data = await _load_run_data(run_id)
        gen = DashboardGenerator(output_dir="/app/artifacts/dashboards")
        paths = gen.generate_all_presets(run_data)

        results = []
        for preset, filepath in paths.items():
            filename = os.path.basename(filepath)
            results.append(DashboardResponse(
                dashboard_id=filename.replace(".html", ""),
                preset=preset,
                file_path=filepath,
                url=f"/artifacts/dashboards/{filename}",
                chart_count=0,
                generated_at="",
            ))
        return results
    except Exception as e:
        logger.error("Batch dashboard generation failed: %s", e)
        raise HTTPException(500, str(e))


@router.get("/presets")
async def list_presets():
    """List available stakeholder presets with descriptions."""
    return {
        "presets": [
            {"key": "executive", "name": "Executive", "description": "KPIs, pass rate trend, risk scores"},
            {"key": "qa_lead", "name": "QA Lead", "description": "Coverage heatmap, failure clusters, flaky tests"},
            {"key": "developer", "name": "Developer", "description": "Error types, stack traces, test durations"},
            {"key": "release", "name": "Release Manager", "description": "Gate status, blockers, sign-off progress"},
            {"key": "product", "name": "Product Owner", "description": "Feature coverage, user story mapping"},
        ]
    }


@router.post("/gate-check", response_model=GateCheckResponse)
async def release_gate_check(req: GateCheckRequest):
    """Evaluate release gate for a test run."""
    try:
        from src.reporting.release_gate import ReleaseGate

        run_data = await _load_run_data(req.run_id)
        gate = ReleaseGate(profile=req.profile)
        decision = gate.evaluate(run_data)
        d = decision.to_dict()

        return GateCheckResponse(
            verdict=d["verdict"],
            score=d["score"],
            confidence=d["confidence"],
            blocking_count=len(decision.blocking_rules),
            warning_count=len(decision.warning_rules),
            ci_exit_code=decision.ci_exit_code,
            rules=d["rules"],
        )
    except Exception as e:
        logger.error("Gate check failed: %s", e)
        raise HTTPException(500, str(e))


@router.get("/gate-profiles")
async def list_gate_profiles():
    """List available release gate profiles."""
    return {
        "profiles": [
            {"key": "strict", "description": "95% pass rate, zero critical bugs, 80% coverage"},
            {"key": "standard", "description": "85% pass rate, zero critical bugs, 70% coverage"},
            {"key": "relaxed", "description": "70% pass rate, max 2 critical bugs, 50% coverage"},
        ]
    }


# -- Helpers ------------------------------------------------------------------

async def _load_run_data(run_id: str) -> Dict[str, Any]:
    """Load run data from database or file. Stub for integration."""
    import json
    # Try file-based first (for dev/testing)
    artifacts_dir = os.getenv("ARTIFACTS_DIR", "/app/artifacts")
    run_file = os.path.join(artifacts_dir, f"run_{run_id}.json")
    if os.path.exists(run_file):
        with open(run_file) as f:
            return json.load(f)

    # TODO: Load from PostgreSQL via session store
    # from src.session.session_store import SessionStore
    # store = SessionStore()
    # return await store.get_run(run_id)

    raise HTTPException(404, f"Run {run_id} not found")
