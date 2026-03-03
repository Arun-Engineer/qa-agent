import datetime
import inspect
import json
import os
import logging
from pathlib import Path

from rich import print as rprint

from agent.planner import Planner
from agent.codegen.generator import TestGenerator
from agent.tools import pytest_runner, playwright_runner, api_caller, bug_reporter
from agent.utils.reporting import export_run_artifacts

logger = logging.getLogger(__name__)


def _filter_args(func, args_dict: dict) -> dict:
    sig = inspect.signature(func)
    allowed = sig.parameters.keys()
    return {k: v for k, v in (args_dict or {}).items() if k in allowed}


def _json_default(o):
    """Make logs/artifacts JSON-safe (Path, datetime, exceptions, etc.)."""
    if isinstance(o, Path):
        return str(o)
    if isinstance(o, (datetime.datetime, datetime.date)):
        return o.isoformat()
    if isinstance(o, Exception):
        return {"type": type(o).__name__, "message": str(o)}
    return str(o)


def _save_run_history(summary: dict) -> None:
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


def _get_real_counts_from_report(detailed_results: list) -> dict:
    """
    Try to read actual pass/fail counts from pytest-json-report output
    instead of relying on the runner's summary (which may just show 1 fail for errors).
    """
    for item in detailed_results:
        res = (item or {}).get("result") or {}
        rf = res.get("report_file")
        if rf:
            p = Path(rf)
            if p.exists():
                try:
                    report = json.loads(p.read_text(encoding="utf-8"))
                    summary = report.get("summary") or {}
                    return {
                        "passed": int(summary.get("passed", 0) or 0),
                        "failed": int(summary.get("failed", 0) or 0),
                        "skipped": int(summary.get("skipped", 0) or 0),
                        "errors": int(summary.get("error", 0) or summary.get("errors", 0) or 0),
                        "total": int(summary.get("total", 0) or 0),
                    }
                except Exception:
                    pass

    # Also check data/reports/ for most recent
    reports_dir = Path("data") / "reports"
    if reports_dir.exists():
        jsons = sorted(reports_dir.glob("report_*.json"), reverse=True)
        if jsons:
            try:
                report = json.loads(jsons[0].read_text(encoding="utf-8"))
                summary = report.get("summary") or {}
                return {
                    "passed": int(summary.get("passed", 0) or 0),
                    "failed": int(summary.get("failed", 0) or 0),
                    "skipped": int(summary.get("skipped", 0) or 0),
                    "errors": int(summary.get("error", 0) or summary.get("errors", 0) or 0),
                    "total": int(summary.get("total", 0) or 0),
                }
            except Exception:
                pass

    return None


def _save_logs(spec: str, plan: dict, detailed_results: list, response: dict) -> dict:
    """
    Creates artifacts (run json, report json, pdf, xlsx) via export_run_artifacts().
    Always writes a fallback JSON log even if artifact generation fails.
    Returns a dict with filenames/paths so UI/dashboard can link them.
    """
    out_dir = Path(os.getenv("ARTIFACTS_DIR", str(Path("data") / "logs")))
    out_dir.mkdir(parents=True, exist_ok=True)

    os.environ.setdefault("QA_PROJECT_NAME", "AI QA Platform Demo")
    os.environ.setdefault("QA_ENVIRONMENT", os.getenv("QA_ENVIRONMENT", os.getenv("MODE", "Local")))
    os.environ.setdefault("QA_BUILD_VERSION", os.getenv("QA_BUILD_VERSION", "Sample v0.1"))
    os.environ.setdefault("QA_PREPARED_BY", os.getenv("QA_PREPARED_BY", "QA Agent (Automated)"))

    ts = datetime.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    fallback_run_json = out_dir / f"run_{ts}.json"

    payload = {
        "spec": spec,
        "plan": plan,
        "results": detailed_results,
        "response": response,
        "timestamp": response.get("timestamp"),
    }

    # Always write a JSON fallback
    try:
        fallback_run_json.write_text(
            json.dumps(payload, indent=2, default=_json_default),
            encoding="utf-8",
        )
    except Exception as e:
        logger.error(f"Failed to write fallback run JSON: {e}")

    # Primary artifact generation (PDF/XLSX etc.)
    try:
        artifacts = export_run_artifacts(spec, plan, detailed_results)

        return {
            "run_id": getattr(artifacts, "run_id", None),
            "run_json": str(getattr(artifacts, "run_json", fallback_run_json.name)),
            "report_json": str(getattr(artifacts, "report_json", "")) or None,
            "pdf": str(getattr(artifacts, "pdf", "")) or None,
            "xlsx": str(getattr(artifacts, "xlsx", "")) or None,
            "fallback_run_json": str(fallback_run_json.name),
        }
    except Exception as e:
        # LOG THE ERROR instead of silently swallowing it
        logger.error(f"Artifact generation failed: {type(e).__name__}: {e}", exc_info=True)
        rprint(f"[red]❌ Artifact generation failed: {type(e).__name__}: {e}[/red]")
        return {
            "run_id": None,
            "run_json": str(fallback_run_json.name),
            "report_json": None,
            "pdf": None,
            "xlsx": None,
            "fallback_run_json": str(fallback_run_json.name),
            "artifact_error": {"type": type(e).__name__, "message": str(e)},
        }


def explain_mode(question: str) -> str:
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


def run_agent_from_spec(spec: str, html: bool = False, trace: bool = False) -> dict:
    planner = Planner()
    plan = planner.generate_plan(spec)

    if "steps" not in plan:
        return {"status": "failed", "error": plan, "timestamp": datetime.datetime.utcnow().isoformat()}

    detailed_results = []
    passed = failed = 0

    for step in plan["steps"]:
        tool = step.get("tool")
        args = step.get("args", {}) or {}

        # ALWAYS regenerate test files to use latest prompt + post-processor
        if tool in ("pytest_runner", "playwright_runner") and "path" in args:
            p = Path(args["path"])
            force_regen = os.getenv("FORCE_REGEN_TESTS", "1") == "1"  # Default ON

            if force_regen or not p.exists():
                rprint(f"[yellow]⚠️  Generating test file: {p}[/yellow]")
                try:
                    codegen = TestGenerator()
                    gen_kwargs = {"step": step, "spec": spec}
                    sig = inspect.signature(codegen.generate_test_code)

                    if "site_model_path" in sig.parameters:
                        site_model_path = (
                            args.get("site_model_path")
                            or step.get("site_model_path")
                            or (step.get("understanding") or {}).get("site_model_path")
                        )
                        gen_kwargs["site_model_path"] = site_model_path

                    if "fix_error" in sig.parameters:
                        gen_kwargs["fix_error"] = None

                    code = codegen.generate_test_code(**gen_kwargs)
                    codegen.write_test_file(code, p)
                    rprint(f"[green]✅ Test file generated: {p}[/green]")
                except Exception as e:
                    logger.error(f"Code generation failed: {e}", exc_info=True)
                    rprint(f"[red]❌ Code generation failed: {e}[/red]")

        # Run tools
        if tool == "pytest_runner":
            safe = _filter_args(pytest_runner.run_pytest, args)
            res = pytest_runner.run_pytest(**safe)

        elif tool == "playwright_runner":
            safe = _filter_args(playwright_runner.run_playwright, args)
            res = playwright_runner.run_playwright(**safe)

        elif tool == "api_caller":
            res = api_caller.call_api(**args)

        elif tool == "bug_reporter":
            safe = {k: v for k, v in args.items() if k in ["title", "severity", "details", "steps_to_reproduce"]}
            res = bug_reporter.file_bug(**safe)

        else:
            res = {"status": "skipped", "error": f"Unsupported tool: {tool}"}

        detailed_results.append({"step": step, "result": res})

        # Aggregate counts from pytest/playwright summaries
        if isinstance(res, dict) and res.get("summary"):
            s = res["summary"] or {}
            passed += int(s.get("passed", 0) or 0)
            failed += int(s.get("failed", 0) or 0)

    # Try to get REAL counts from pytest-json-report (more accurate)
    real_counts = _get_real_counts_from_report(detailed_results)
    if real_counts:
        passed = real_counts["passed"]
        failed = real_counts["failed"] + real_counts.get("errors", 0)

    response = {
        "status": "completed",
        "goal": plan.get("goal"),
        "assumptions": plan.get("assumptions", []),
        "total_steps": len(plan["steps"]),
        "passed": passed,
        "failed": failed,
        "results": detailed_results,
        "timestamp": datetime.datetime.utcnow().isoformat(),
    }

    # Generate artifacts (PDF + Excel + JSON)
    artifacts = _save_logs(spec=spec, plan=plan, detailed_results=detailed_results, response=response)
    response["artifacts"] = artifacts

    # Save run history for dashboard
    _save_run_history(
        {
            "run_id": artifacts.get("run_id"),
            "goal": response.get("goal"),
            "passed": response.get("passed"),
            "failed": response.get("failed"),
            "timestamp": response.get("timestamp"),
            "pdf": artifacts.get("pdf"),
            "xlsx": artifacts.get("xlsx"),
            "run_json": artifacts.get("run_json"),
            "report_json": artifacts.get("report_json"),
        }
    )

    return response
