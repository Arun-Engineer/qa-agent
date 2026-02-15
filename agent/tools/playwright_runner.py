# agent/tools/playwright_runner.py
import json
import subprocess
import sys
from pathlib import Path
from datetime import datetime


def run_playwright(path: str, timeout: int = 300):
    p = Path(path)

    if not p.exists():
        return {"status": "error", "error": f"Test file not found: {p}"}

    reports_dir = Path("data") / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    run_id = datetime.utcnow().strftime("%Y%m%d_%H%M%S_%f")
    report_file = reports_dir / f"report_{run_id}.json"
    junit_file = reports_dir / f"junit_{run_id}.xml"

    cmd = [
        sys.executable, "-m", "pytest", str(p),
        "-q",
        "--json-report",
        f"--json-report-file={report_file}",
        f"--junitxml={junit_file}",
    ]

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"status": "error", "error": f"Timeout after {timeout}s"}
    except FileNotFoundError as e:
        return {"status": "error", "error": f"Executable not found: {e}"}

    summary = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0}
    if report_file.exists():
        try:
            report = json.loads(report_file.read_text(encoding="utf-8"))
            s = report.get("summary", {}) or {}
            summary = {
                "passed": int(s.get("passed", 0) or 0),
                "failed": int(s.get("failed", 0) or 0),
                "errors": int(s.get("errors", 0) or 0),
                "skipped": int(s.get("skipped", 0) or 0),
            }
        except Exception:
            pass
    else:
        # fallback if json-report isn't generated
        summary = {
            "passed": 1 if proc.returncode == 0 else 0,
            "failed": 0 if proc.returncode == 0 else 1,
            "errors": 0,
            "skipped": 0,
        }

    return {
        "status": "passed" if proc.returncode == 0 else "failed",
        "summary": summary,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "exit_code": proc.returncode,
        "report_file": str(report_file),
        "junit_file": str(junit_file),
    }
