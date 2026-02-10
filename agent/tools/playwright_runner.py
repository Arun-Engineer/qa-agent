# agent/tools/playwright_runner.py
import subprocess

def run_playwright(path: str):
    try:
        result = subprocess.run(["npx", "playwright", "test", path], capture_output=True, timeout=60)
        return {
            "status": "completed",
            "code": result.returncode,
            "stdout": result.stdout.decode(),
            "stderr": result.stderr.decode()
        }
    except subprocess.TimeoutExpired:
        return {"status": "timeout", "error": "Playwright test timed out."}
    except Exception as e:
        return {"status": "error", "error": str(e)}
