# agent/tools/pytest_runner.py
import subprocess
import json
from pathlib import Path

def run_pytest(path: str):
    try:
        result = subprocess.run([
            "pytest", path, "--disable-warnings", "--maxfail=1", "--tb=short", "--json-report"
        ], capture_output=True, text=True, timeout=45)

        report_path = Path(".report.json")
        report_data = {}
        if report_path.exists():
            try:
                report_data = json.loads(report_path.read_text())
            except Exception:
                report_data = {"error": "Failed to parse .report.json"}

        return {
            "code": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "summary": parse_report_summary(report_data)
        }

    except subprocess.TimeoutExpired:
        return {
            "code": 1,
            "error": "Test execution timed out."
        }
    except Exception as e:
        return {
            "code": 1,
            "error": str(e)
        }

def parse_report_summary(report: dict):
    summary = report.get("summary", {})
    return {
        "passed": summary.get("passed", 0),
        "failed": summary.get("failed", 0),
        "skipped": summary.get("skipped", 0),
        "total": summary.get("total", 0)
    }
