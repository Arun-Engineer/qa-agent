import argparse
import json
import datetime
import os
import webbrowser
from pathlib import Path
from rich import print as rprint
from rich.panel import Panel
from fpdf import FPDF
from agent.planner import Planner
from agent.tools import pytest_runner, playwright_runner, api_caller, bug_reporter
from agent.codegen.generator import TestGenerator
from agent.integrations.slack_notifier import send_slack_alert
from agent.utils import memory as default_memory
from agent.extenstions import vector_memory
from agent.ticket_router import fetch_ticket
from openai import OpenAI

def ensure_conftest_base_url():
    cf = Path("conftest.py")
    if cf.exists():
        return
    cf.write_text(
        'import os\n'
        'import pytest\n\n'
        '@pytest.fixture(scope="session")\n'
        'def base_url() -> str:\n'
        '    url = (os.getenv("BASE_URL") or os.getenv("APP_BASE_URL") or "").strip()\n'
        '    if not url:\n'
        '        url = "https://example.com"\n'
        '    return url.rstrip("/")\n',
        encoding="utf-8"
    )


def explain_mode(question: str) -> str:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is missing")

    client = OpenAI(api_key=api_key)

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a highly experienced Senior QA Architect. "
                    "Explain software testing concepts clearly, practically, "
                    "and concisely with real-world examples."
                ),
            },
            {"role": "user", "content": question},
        ],
        temperature=0.3,
    )

    answer = (response.choices[0].message.content or "").strip()
    if not answer:
        raise RuntimeError("Model returned empty answer")

    return answer

def save_run_history(run_data):
    path = Path("data/runs.json")
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.exists():
        existing = json.loads(path.read_text())
    else:
        existing = []

    existing.append(run_data)
    path.write_text(json.dumps(existing, indent=2))


def run_agent_from_spec(spec: str, html: bool = False, trace: bool = False):
    planner = Planner()
    plan = planner.generate_plan(spec)

    if "steps" not in plan:
        return {"status": "failed", "error": plan}

    detailed_results = []
    passed = 0
    failed = 0

    for step in plan["steps"]:
        result = execute_step(step, spec=spec,html=html, trace=trace)

        detailed_results.append({
            "step": step,
            "result": result
        })

        if result.get("summary"):
            s = result["summary"]
            passed += s.get("passed", 0)
            failed += s.get("failed", 0)

    response = {
        "status": "completed",
        "goal": plan.get("goal"),
        "total_steps": len(plan["steps"]),
        "passed": passed,
        "failed": failed,
        "timestamp": datetime.datetime.utcnow().isoformat()
    }

    # Save summary for dashboard
    save_run_history(response)

    # Save detailed logs + PDF
    save_logs(spec, plan, detailed_results)

    return response

def filter_args(func, args_dict):
    """
    Pass only supported kwargs to tool functions
    """
    import inspect
    sig = inspect.signature(func)
    allowed = sig.parameters.keys()
    return {k: v for k, v in args_dict.items() if k in allowed}

def execute_step(step, spec, html=False, trace=False):
    tool_name = step.get("tool")
    tool_args = step.get("args", {}) or {}

    # -----------------------------
    # 1) Generate/Overwrite tests
    # -----------------------------
    should_regenerate = False
    path = None

    if tool_name in ["pytest_runner", "playwright_runner"] and "path" in tool_args:
        path = Path(tool_args["path"])

        # Force overwrite for generated tests so old broken files don't keep running
        should_regenerate = ("tests" in path.parts and "generated" in path.parts)

        if should_regenerate or not path.exists():
            rprint(f"[yellow]⚠️  Generating test file: {path}[/yellow]")
            codegen = TestGenerator()
            # pass spec + step so generator can produce correct UI/API test
            test_code = codegen.generate_test_code(step=step, spec=spec)
            saved_path = codegen.write_test_file(test_code, path)
            rprint(f"[green]📄 Generated and saved: {saved_path}[/green]")

    # -----------------------------
    # helper to run selected tool
    # -----------------------------
    def _run_tool():
        if tool_name == "pytest_runner":
            safe_args = filter_args(pytest_runner.run_pytest, tool_args)
            return pytest_runner.run_pytest(**safe_args)

        if tool_name == "playwright_runner":
            # bridge step.args.base_url -> pytest-playwright base_url fixture via env
            step_url = (tool_args.get("base_url") or "").strip()
            if step_url:
                step_url = step_url.rstrip("/")
                os.environ["BASE_URL"] = step_url
                os.environ["APP_BASE_URL"] = step_url

            ensure_conftest_base_url()

            tool_args["trace"] = trace
            safe_args = filter_args(playwright_runner.run_playwright, tool_args)
            return playwright_runner.run_playwright(**safe_args)

        if tool_name == "api_caller":
            return api_caller.call_api(**tool_args)

        if tool_name == "bug_reporter":
            safe_args = {k: v for k, v in tool_args.items()
                         if k in ["title", "severity", "details", "steps_to_reproduce"]}
            return bug_reporter.file_bug(**safe_args)

        return {"status": "error", "error": f"Unsupported tool: {tool_name}"}

    # -----------------------------
    # 2) Run tool
    # -----------------------------
    result = _run_tool()

    # open report if requested
    if tool_name == "pytest_runner" and html and Path("report.html").exists():
        webbrowser.open("report.html")

    # -----------------------------
    # 3) Playwright failure capture
    # -----------------------------
    if tool_name == "playwright_runner":
        if result.get("status") in ["failed", "error"] and result.get("screenshot"):
            try:
                default_memory.save_artifact(
                    "playwright_failure",
                    result["screenshot"].encode(),
                    ext=".png",
                )
            except Exception:
                pass

            try:
                vector_memory.store_memory(
                    text=f"Playwright failure: {step}\nError: {result.get('error','')}",
                    metadata={"tool": "playwright_runner"},
                )
            except Exception:
                pass

    # -----------------------------
    # 4) Auto-repair (regen once)
    #    only for generated tests
    # -----------------------------
    if should_regenerate and path is not None:
        fixable_patterns = (
            "ScopeMismatch",
            'Fixture "base_url" called directly',
            "SyntaxError",
            "IndentationError",
            "NameError",
        )
        stdout = (result.get("stdout") or "")
        stderr = (result.get("stderr") or "")
        err_text = (result.get("error") or "")
        combined = f"{stdout}\n{stderr}\n{err_text}"

        if any(p in combined for p in fixable_patterns):
            rprint("[yellow]🔁 Auto-repairing generated test once...[/yellow]")
            codegen = TestGenerator()
            # Give the failure back to generator as feedback
            repaired_code = codegen.generate_test_code(step=step, spec=spec, fix_error=combined)
            codegen.write_test_file(repaired_code, path)
            result = _run_tool()

            # If it's Playwright and failed again, capture again
            if tool_name == "playwright_runner":
                if result.get("status") in ["failed", "error"] and result.get("screenshot"):
                    try:
                        default_memory.save_artifact(
                            "playwright_failure",
                            result["screenshot"].encode(),
                            ext=".png",
                        )
                    except Exception:
                        pass
                    try:
                        vector_memory.store_memory(
                            text=f"Playwright failure after auto-repair: {step}\nError: {result.get('error','')}",
                            metadata={"tool": "playwright_runner"},
                        )
                    except Exception:
                        pass

    return result


def save_logs(spec, plan, results):
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path("data/logs")
    out_dir.mkdir(parents=True, exist_ok=True)

    report_json = Path(".report.json")
    if report_json.exists():
        report_copy = out_dir / f"report_{timestamp}.json"
        report_copy.write_text(report_json.read_text())

    log = {
        "timestamp": timestamp,
        "spec": spec,
        "plan": plan,
        "results": results
    }
    path = out_dir / f"run_{timestamp}.json"
    path.write_text(json.dumps(log, indent=2))
    rprint(f"\n[bold green]📄 Log saved to:[/bold green] {path}")

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", size=12)
    pdf.cell(200, 10, txt="QA Agent Test Report", ln=True, align="C")
    pdf.ln(10)
    pdf.multi_cell(0, 8, f"Timestamp: {log['timestamp']}\nGoal: {log['plan'].get('goal', '')}")
    pdf.ln(5)
    for i, entry in enumerate(log["results"], 1):
        step = entry["step"]
        result = entry["result"]
        pdf.multi_cell(0, 8, f"Step {i}: {step['tool']}\nArgs: {step['args']}\nResult: {result}\n---")
    pdf_path = out_dir / f"run_{timestamp}.pdf"
    pdf.output(str(pdf_path))
    rprint(f"[cyan]📎 PDF exported:[/cyan] {pdf_path}")


def print_result_summary(result):
    if "summary" in result:
        s = result["summary"]
        passed, failed, skipped = s.get("passed", 0), s.get("failed", 0), s.get("skipped", 0)
        rprint(Panel.fit(
            f"🟢 Passed: {passed}    🔴 Failed: {failed}    ⚪ Skipped: {skipped}",
            title="[cyan]Test Summary[/cyan]",
            border_style="bright_blue"
        ))
        if failed > 0:
            send_slack_alert(f"❗ Test run failed with {failed} failing cases. Summary: Passed={passed}, Skipped={skipped}")


def main():
    parser = argparse.ArgumentParser(description="QA Agent CLI")
    parser.add_argument("--spec", type=str, help="Test spec as input text")
    parser.add_argument("--file", type=str, help="Path to file containing test spec")
    parser.add_argument("--ticket", type=str, help="Ticket ID or URL from Jira, GitHub, or Azure DevOps")
    parser.add_argument("--explain", type=str, help="Ask QA theory question")
    parser.add_argument("--html", action="store_true", help="Auto-open pytest HTML report if available")
    parser.add_argument("--trace", action="store_true", help="Enable trace logging for browser-based steps")
    args = parser.parse_args()

    # 📚 Explain mode
    if args.explain:
        explain_mode(args.explain)
        return

    # 📄 Ticket mode
    if args.ticket:
        ticket_data = fetch_ticket(args.ticket)
        composed_spec = f"{ticket_data['title']}\n\n{ticket_data['description']}\n\n{ticket_data.get('repro_steps', '')}"
        run_agent_from_spec(composed_spec, html=args.html, trace=args.trace)
        return

    # 📝 Spec or file
    if args.spec:
        spec = args.spec
    elif args.file:
        spec = Path(args.file).read_text(encoding="utf-8")
    else:
        rprint("[red]❌ Please provide one of: --spec, --file, or --ticket[/red]")
        return

    run_agent_from_spec(spec, html=args.html, trace=args.trace)

if __name__ == "__main__":
    main()
