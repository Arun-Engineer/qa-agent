"""src/api/routes/autonomous.py — REST surface for the autonomous QA agent.

Endpoints (mounted under /api/v1/auto):

    POST /start
        body: {"url": "https://app.example.com", "scope": {"max_pages": 30}}
        -> {"run_id": "...", "state": "RUNNING"}

    GET  /{run_id}/status
        -> {...full status snapshot including pending credential prompts...}

    POST /{run_id}/credentials
        body: {"role": "user", "username": "...", "password": "...",
               "totp_seed": "optional"}
        -> {"ok": true, "state": "RUNNING"|"NEEDS_CREDS"}
        (If additional roles still need creds, state stays NEEDS_CREDS.)

    POST /{run_id}/cancel
        -> {"ok": true}

    GET  /{run_id}/stream
        SSE stream of events for the run (for the UI to tail progress live).

Security:
  - Credentials are accepted only over HTTPS in production (enforce at proxy).
  - Never logged, never stored to disk. See cred_vault for lifecycle.
  - Runs are scoped by whatever session/tenancy middleware is already mounted
    upstream of this router; the endpoints themselves are intentionally thin.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from agent.workflows import autonomous_qa


router = APIRouter()


# ── Request/Response shapes ───────────────────────────────────────────────

class StartRequest(BaseModel):
    url: str = Field(..., description="Root URL of the app to test")
    scope: Optional[dict] = Field(default=None,
                                   description="Optional budgets: max_pages, max_depth")


class CredentialRequest(BaseModel):
    role: str = Field(..., description="Which role these creds belong to")
    username: str = ""
    password: str = ""
    totp_seed: str = ""
    extras: Optional[dict] = None


# ── Endpoints ─────────────────────────────────────────────────────────────

@router.post("/start")
async def start(req: StartRequest) -> dict:
    if not req.url or not req.url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="url must be http(s)://…")
    ctx = autonomous_qa.start_run(req.url, scope=req.scope)
    return {"run_id": ctx.run_id, "state": ctx.state, "url": ctx.url}


@router.get("/{run_id}/status")
async def status(run_id: str) -> dict:
    ctx = autonomous_qa.get_run(run_id)
    if not ctx:
        raise HTTPException(status_code=404, detail="unknown run_id")
    return ctx.to_status()


@router.post("/{run_id}/credentials")
async def credentials(run_id: str, req: CredentialRequest) -> dict:
    ok = autonomous_qa.provide_credentials(
        run_id, req.role, req.username, req.password,
        totp_seed=req.totp_seed, extras=req.extras,
    )
    if not ok:
        raise HTTPException(status_code=404, detail="run not found or already terminal")
    ctx = autonomous_qa.get_run(run_id)
    return {"ok": True, "state": ctx.state if ctx else "unknown"}


@router.post("/{run_id}/cancel")
async def cancel(run_id: str) -> dict:
    ok = autonomous_qa.cancel_run(run_id)
    if not ok:
        raise HTTPException(status_code=404, detail="run not found or already terminal")
    return {"ok": True}


@router.get("/runs")
async def list_runs() -> dict:
    return {"runs": autonomous_qa.list_runs()}


class ReplayRequest(BaseModel):
    run_id: str = Field(..., description="Original run_id to replay")
    tenant_id: str = "default"


@router.get("/observability-adapters")
async def observability_adapters() -> dict:
    """List every MLOps / agent-observability platform the probe supports.

    The UI feeds this into the Platform dropdown so users can pick a
    specific vendor (Puvi, LangSmith, Langfuse, Arize Phoenix…) or leave
    it blank for heuristic auto-detection.
    """
    from agent.integrations.observability import registry as adapter_registry
    return {"adapters": adapter_registry.list_adapters()}


@router.get("/knowledge")
async def knowledge(tenant_id: str = "default") -> dict:
    """What the agent has learned across its lifetime for this tenant.

    Aggregates every persistent memory channel so the UI can display a
    single "agent brain" view: selector hits, promoted hypotheses, flake
    leaders, baselines, platform profiles, recent runs.
    """
    out: dict = {"tenant_id": tenant_id}
    try:
        from agent.memory import selector_memory
        out["selectors"] = selector_memory.all_for_tenant(tenant_id)[:200]
    except Exception as e:
        out["selectors_error"] = str(e)
    try:
        from agent.memory import run_intel
        out["recent_runs"] = run_intel.recent(tenant_id, limit=20) \
            if hasattr(run_intel, "recent") else []
        out["flake_leaders"] = run_intel.top_flakes(tenant_id, limit=20) \
            if hasattr(run_intel, "top_flakes") else []
    except Exception as e:
        out["run_intel_error"] = str(e)
    try:
        from agent.oracles import confirmed
        out["baselines"] = [b.__dict__ for b in
                            confirmed.list_for_tenant(tenant_id)][:100]
    except Exception as e:
        out["baselines_error"] = str(e)
    try:
        from agent.memory import platform_profiles
        out["platform_profiles"] = platform_profiles.list_for_tenant(tenant_id)
    except Exception as e:
        out["platform_profiles_error"] = str(e)
    try:
        from agent.oracles import configured
        out["promoted_rules"] = configured.list_for_tenant(tenant_id) \
            if hasattr(configured, "list_for_tenant") else []
    except Exception as e:
        out["promoted_rules_error"] = str(e)
    return out


@router.post("/replay")
async def replay(req: ReplayRequest) -> dict:
    """Spin up a new autonomous run from a snapshotted ApplicationModel.

    Used for regression isolation: re-test the exact same surface the
    original run tested, regardless of what the live site looks like now.
    """
    from agent.workflows.replay import replay_run

    ctx = replay_run(req.run_id, tenant_id=req.tenant_id)
    if not ctx:
        raise HTTPException(status_code=404,
                            detail="no snapshot found for that run_id")
    return {"run_id": ctx.run_id, "state": ctx.state, "url": ctx.url,
            "replay_of": req.run_id}


@router.get("/{run_id}/stream")
async def stream(run_id: str):
    """SSE stream — tails the run's event log until it reaches a terminal state."""
    ctx = autonomous_qa.get_run(run_id)
    if not ctx:
        raise HTTPException(status_code=404, detail="unknown run_id")

    def _sse(event: str, data) -> str:
        return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"

    async def gen():
        yield _sse("start", {"run_id": run_id, "ts": datetime.utcnow().isoformat()})
        last_sent = 0
        while True:
            cur = autonomous_qa.get_run(run_id)
            if not cur:
                yield _sse("error", {"message": "run disappeared"})
                break
            # Emit any new events since the last cursor.
            new_events = cur.events[last_sent:]
            for ev in new_events:
                yield _sse("event", ev)
            last_sent = len(cur.events)

            if cur.state in autonomous_qa.TERMINAL:
                yield _sse("done", {"state": cur.state,
                                    "status": cur.to_status()})
                break
            if cur.state == autonomous_qa.State.NEEDS_CREDS:
                # Still emit a heartbeat so the UI knows we're alive.
                yield _sse("needs_credentials",
                           {"pending": cur.to_status()["pending_prompts"]})
            await asyncio.sleep(1.0)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
