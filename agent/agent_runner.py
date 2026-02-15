import datetime
import inspect
import json
import os
from pathlib import Path

from rich import print as rprint

from agent.planner import Planner
from agent.codegen.generator import TestGenerator
from agent.tools import pytest_runner, playwright_runner, api_caller, bug_reporter


def _filter_args(func, args_dict: dict) -> dict:
    sig = inspect.signature(func)
    allowed = sig.parameters.keys()
    return {k: v for k, v in (args_dict or {}).items() if k in allowed}


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
    path.write_text(json.dumps(existing, indent=2), encoding="utf-8")


def _save_logs(spec: str, plan: dict, detailed_results: list, response: dict) -> None:
    ts = datetime.datetime.utcnow().strftime("%Y%m%d_%H%M%S_%f")
    out_dir = Path("data") / "logs"
    out_dir.mkdir(parents=True, exist_ok=True)

    log = {
        "timestamp": ts,
        "spec": spec,
        "plan": plan,
        "response": response,
        "results": detailed_results,
    }

    path = out_dir / f"run_{ts}.json"
    path.write_text(json.dumps(log, indent=2), encoding="utf-8")


def explain_mode(question: str) -> str:
    from openai import OpenAI
    client = OpenAI()
    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "You are a Senior QA Architect. Explain clearly with examples."},
            {"role": "user", "content": question},
        ],
        temperature=0.3,
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

        # auto-generate missing tests (do NOT overwrite by default — it kills demo speed)
        if tool in ("pytest_runner", "playwright_runner") and "path" in args:
            p = Path(args["path"])

            # Only overwrite when explicitly forced
            force_regen = os.getenv("FORCE_REGEN_TESTS", "0") == "1"

            if force_regen or not p.exists():
                rprint(f"[yellow]⚠️  Generating test file: {p}[/yellow]")
                codegen = TestGenerator()

                # Build kwargs safely (prevents unexpected kwarg errors)
                gen_kwargs = {"step": step, "spec": spec}

                sig = inspect.signature(codegen.generate_test_code)

                # site_model_path may exist in args OR in step["understanding"]
                if "site_model_path" in sig.parameters:
                    site_model_path = (
                            args.get("site_model_path")
                            or step.get("site_model_path")
                            or (step.get("understanding") or {}).get("site_model_path")
                    )
                    gen_kwargs["site_model_path"] = site_model_path

                # (optional) if you later add fix_error loops, keep this safe
                if "fix_error" in sig.parameters:
                    gen_kwargs["fix_error"] = None

                code = codegen.generate_test_code(**gen_kwargs)
                codegen.write_test_file(code, p)
        # run tools with SAFE args
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

        if isinstance(res, dict) and res.get("summary"):
            s = res["summary"] or {}
            passed += int(s.get("passed", 0) or 0)
            failed += int(s.get("failed", 0) or 0)

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

    # ✅ THIS is what makes dashboard + history work
    _save_run_history({
        "goal": response["goal"],
        "passed": response["passed"],
        "failed": response["failed"],
        "timestamp": response["timestamp"],
    })

    _save_logs(spec=spec, plan=plan, detailed_results=detailed_results, response=response)

    return response
