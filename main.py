import argparse
import json
import datetime
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

def run_agent_from_spec(spec: str, html: bool = False, trace: bool = False):
    planner = Planner()
    plan = planner.generate_plan(spec)

    if "steps" not in plan:
        rprint("[red]❌ Plan generation failed:[/red]", plan)
        return

    results = []
    for idx, step in enumerate(plan["steps"], 1):
        rprint(f"\n[bold cyan]🔧 Step {idx}:[/bold cyan] {step['tool']} | Args: {step['args']}")
        result = execute_step(step, html=html, trace=trace)
        results.append({"step": step, "result": result})
        print_result_summary(result)

    save_logs(spec, plan, results)

def filter_args(func, args_dict):
    """
    Pass only supported kwargs to tool functions
    """
    import inspect
    sig = inspect.signature(func)
    allowed = sig.parameters.keys()
    return {k: v for k, v in args_dict.items() if k in allowed}

def execute_step(step, html=False, trace=False):
    tool_name = step.get("tool")
    tool_args = step.get("args", {})

    if tool_name in ["pytest_runner", "playwright_runner"] and "path" in tool_args:
        path = Path(tool_args["path"])
        if not path.exists():
            rprint(f"[yellow]⚠️  Test file missing: {path} — generating...[/yellow]")
            codegen = TestGenerator()
            test_code = codegen.generate_test_code(step)
            saved_path = codegen.write_test_file(test_code, path)
            rprint(f"[green]📄 Generated and saved: {saved_path}[/green]")

    if tool_name == "pytest_runner":
        safe_args = filter_args(pytest_runner.run_pytest, tool_args)
        result = pytest_runner.run_pytest(**safe_args)
        if html and Path("report.html").exists():
            webbrowser.open("report.html")
        return result


    elif tool_name == "playwright_runner":

        tool_args["trace"] = trace

        safe_args = filter_args(playwright_runner.run_playwright, tool_args)
        result = playwright_runner.run_playwright(**safe_args)

        if result.get("status") == "failed" and result.get("screenshot"):
            # Save artifact locally (short-term)

            default_memory.save_artifact(

                "playwright_failure",

                result["screenshot"].encode(),

                ext=".png"

            )

            # Store semantic memory (long-term vector)

            vector_memory.store_memory(

                text=f"Playwright failure: {step}",

                metadata={"tool": "playwright_runner"}

            )

        return result

    elif tool_name == "api_caller":
        return api_caller.call_api(**tool_args)

    elif tool_name == "bug_reporter":
        return bug_reporter.file_bug(**tool_args)

    else:
        return {"error": f"Unsupported tool: {tool_name}"}


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
    parser.add_argument("--html", action="store_true", help="Auto-open pytest HTML report if available")
    parser.add_argument("--trace", action="store_true", help="Enable trace logging for browser-based steps")
    args = parser.parse_args()

    if args.spec:
        spec = args.spec
    elif args.file:
        spec = Path(args.file).read_text()
    else:
        rprint("[red]❌ Please provide either --spec or --file[/red]")
        return

    run_agent_from_spec(spec, html=args.html, trace=args.trace)


if __name__ == "__main__":
    main()
