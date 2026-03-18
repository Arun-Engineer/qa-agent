"""
agent/agent_runner.py — Updated to use the Orchestration Engine

Backward compatible:
  - run_agent_from_spec() still works exactly as before
  - explain_mode() unchanged
  - All existing API endpoints keep working

New:
  - Internally uses Orchestrator + Workflows
  - Adds retry, timeout, error handling
  - Supports workflow selection (api_test, ui_test, spec_review)
  - Passes login_wall and scenario context through to orchestrator
"""
from __future__ import annotations

import datetime
import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

from agent.core.orchestrator import Orchestrator, OrchestratorConfig, RunResult, OrchestratorEvent, EventType
from agent.workflows import get_workflow


def _json_default(o):
    """Make logs/artifacts JSON-safe."""
    if isinstance(o, Path):
        return str(o)
    if isinstance(o, (datetime.datetime, datetime.date)):
        return o.isoformat()
    if isinstance(o, Exception):
        return {"type": type(o).__name__, "message": str(o)}
    return str(o)


def _save_run_history(summary: dict) -> None:
    """Append run summary to data/runs.json for dashboard."""
    path = Path("data") / "runs.json"
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(existing, list):
                existing = []
        except Exception:
            existing = []
    else:
        existing = []

    existing.append(summary)
    path.write_text(json.dumps(existing, indent=2, default=_json_default), encoding="utf-8")


def run_agent_from_spec(
    spec: str,
    html: bool = False,
    trace: bool = False,
    workflow_name: str = "default",
    context: Optional[Dict[str, Any]] = None,
) -> dict:
    """
    Main entry point — backward compatible with existing API.

    New parameter:
      workflow_name: "api_test" | "ui_test" | "spec_review" | "default"
      context: optional dict with tenant_id, account_id, etc.

    Fix: Pre-runs understanding_layer so login_wall detection and scenario
    counting happen BEFORE the orchestrator starts. This context is passed
    into the workflow so the planner gets it at plan-time.
    """
    context = context or {}

    # ── Pre-flight: understanding layer runs here so context is available
    # to the orchestrator/workflow before planning begins.
    # The enriched spec (with advisory recon appended) is what gets planned.
    enriched_spec = spec
    try:
        from agent.understanding_layer import enrich_spec_with_understanding
        enriched_spec, understanding_ctx = enrich_spec_with_understanding(spec)
        # Merge understanding context into the run context
        context.setdefault("login_wall_detected", understanding_ctx.login_wall_detected)
        context.setdefault("user_scenarios", understanding_ctx.user_scenarios)
        context.setdefault("base_url", understanding_ctx.base_url)
        context.setdefault("recon_status", understanding_ctx.recon_status)
    except Exception:
        # Understanding layer failure is non-fatal — proceed with raw spec
        pass

    # Configure orchestrator
    config = OrchestratorConfig(
        max_retries=int(os.getenv("QA_MAX_RETRIES", "3")),
        step_timeout=float(os.getenv("QA_STEP_TIMEOUT", "120")),
        planning_timeout=float(os.getenv("QA_PLAN_TIMEOUT", "60")),
        # Disable built-in enrichment since we already ran it above
        enable_enrichment=False,
        artifact_dir=os.getenv("ARTIFACTS_DIR", str(Path("data") / "logs")),
    )

    # Collect console events for optional trace output
    events = []

    def event_handler(event: OrchestratorEvent):
        events.append(event)
        Orchestrator._default_event_handler(event)

    # Get workflow
    try:
        workflow = get_workflow(workflow_name)
    except ValueError:
        workflow = get_workflow("default")

    # Run with enriched spec + full context
    orch = Orchestrator(config=config, on_event=event_handler)
    result = orch.run(spec=enriched_spec, workflow=workflow, context=context)

    # Convert RunResult → legacy response format (backward compatible)
    response = _to_legacy_response(result)

    # Attach understanding summary to response for visibility
    response["understanding"] = {
        "login_wall_detected": context.get("login_wall_detected", False),
        "user_scenario_count": len(context.get("user_scenarios", [])),
        "base_url": context.get("base_url"),
        "recon_status": context.get("recon_status"),
    }

    # Save run history for dashboard
    _save_run_history({
        "run_id": result.run_id,
        "goal": result.goal,
        "workflow": result.workflow,
        "passed": result.passed,
        "failed": result.failed,
        "total_steps": result.total_steps,
        "timestamp": result.started_at,
        "duration_ms": result.duration_ms,
        "pdf": result.artifacts.get("pdf"),
        "xlsx": result.artifacts.get("xlsx"),
        "run_json": result.artifacts.get("run_json"),
        "report_json": result.artifacts.get("report_json"),
        "login_wall_detected": context.get("login_wall_detected", False),
        "user_scenario_count": len(context.get("user_scenarios", [])),
    })

    # Attach trace if requested
    if trace:
        response["trace"] = [
            {"type": e.type.value, "message": e.message, "timestamp": e.timestamp}
            for e in events
        ]

    return response


def _to_legacy_response(result: RunResult) -> dict:
    """Convert new RunResult to the old response dict format."""
    return {
        "status": result.status,
        "goal": result.goal,
        "workflow": result.workflow,
        "assumptions": result.plan.get("assumptions", []),
        "total_steps": result.total_steps,
        "passed": result.passed,
        "failed": result.failed,
        "skipped": result.skipped,
        "results": [
            {
                "step": {
                    "tool": s.tool,
                    "index": s.step_index,
                    "args": (result.plan.get("steps") or [{}])[s.step_index].get("args", {})
                    if s.step_index < len(result.plan.get("steps") or [])
                    else {},
                },
                "result": s.output,
                "status": s.status,
                "error": s.error,
                "duration_ms": s.duration_ms,
                "retries": s.retries,
                "diagnosis": s.diagnosis,
            }
            for s in result.steps
        ],
        "artifacts": result.artifacts,
        "errors": result.errors,
        "timestamp": result.started_at,
        "finished_at": result.finished_at,
        "duration_ms": result.duration_ms,
    }


def explain_mode(question: str) -> str:
    """QA Architect chat — unchanged from original."""
    from agent.utils.openai_wrapper import chat_completion

    resp = chat_completion(
        model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a Senior QA Architect.\n"
                    "Respond in Markdown with clear headings and bullet points.\n"
                    "Rules:\n"
                    "- No paragraph longer than 3 lines.\n"
                    "- Use: ## Summary, ## Key points, ## Example, ## Common mistakes.\n"
                ),
            },
            {"role": "user", "content": question},
        ],
        temperature=0.3,
        service_name="qa-agent-runner",
    )
    return (resp.choices[0].message.content or "").strip()