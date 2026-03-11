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
    """
    # Configure orchestrator
    config = OrchestratorConfig(
        max_retries=int(os.getenv("QA_MAX_RETRIES", "3")),
        step_timeout=float(os.getenv("QA_STEP_TIMEOUT", "120")),
        planning_timeout=float(os.getenv("QA_PLAN_TIMEOUT", "60")),
        enable_enrichment=os.getenv("QA_DISABLE_RECON", "") not in ("1", "true"),
        artifact_dir=os.getenv("ARTIFACTS_DIR", str(Path("data") / "logs")),
    )

    # Collect console events for optional trace output
    events = []

    def event_handler(event: OrchestratorEvent):
        events.append(event)
        # Also print to server console
        Orchestrator._default_event_handler(event)

    # Get workflow
    try:
        workflow = get_workflow(workflow_name)
    except ValueError:
        # Fallback to default
        workflow = get_workflow("default")

    # Run
    orch = Orchestrator(config=config, on_event=event_handler)
    result = orch.run(spec=spec, workflow=workflow, context=context or {})

    # Convert RunResult → legacy response format (backward compatible)
    response = _to_legacy_response(result)

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
                "step": {"tool": s.tool, "index": s.step_index},
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
