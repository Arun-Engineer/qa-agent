from fastapi import FastAPI
from fastapi import HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from agent.agent_runner import run_agent_from_spec, explain_mode
from pydantic import BaseModel
from pathlib import Path
import json
import datetime

#from main import run_agent_from_spec, explain_mode

app = FastAPI(title="AI QA Orchestration Platform")

# -------------------------------------------------------
# Models
# -------------------------------------------------------

class SpecRequest(BaseModel):
    spec: str
    html: bool = False
    trace: bool = False


class ExplainRequest(BaseModel):
    question: str


# -------------------------------------------------------
# Static UI Mount (Production Ready)
# -------------------------------------------------------

if Path("ui").exists():
    app.mount("/static", StaticFiles(directory="ui"), name="static")


@app.get("/")
def serve_dashboard():
    return FileResponse("ui/index.html")


# -------------------------------------------------------
# Execute Spec Endpoint
# -------------------------------------------------------

@app.post("/api/spec")
def run_spec(spec_req: SpecRequest):
    try:
        result = run_agent_from_spec(
            spec_req.spec,
            html=spec_req.html,
            trace=spec_req.trace
        )

        return result

    except Exception as e:
        return {
            "status": "failed",
            "error": str(e),
            "timestamp": datetime.datetime.utcnow().isoformat()
        }


# -------------------------------------------------------
# Explain QA Concept Endpoint
# -------------------------------------------------------

from fastapi import HTTPException

@app.post("/api/run")
def run(req: SpecRequest):
    return run_agent_from_spec(req.spec, html=req.html, trace=req.trace)

@app.post("/api/explain")
def explain(req: ExplainRequest):
    return {"answer": explain_mode(req.question)}

# -------------------------------------------------------
# Run History Endpoint
# -------------------------------------------------------

@app.get("/api/runs")
def get_runs():
    path = Path("data/runs.json")
    if path.exists():
        return json.loads(path.read_text())
    return []


# -------------------------------------------------------
# Metrics Endpoint
# -------------------------------------------------------

@app.get("/api/metrics")
def metrics():
    path = Path("data/runs.json")

    if not path.exists():
        return {
            "total_runs": 0,
            "total_passed": 0,
            "total_failed": 0,
            "average_pass_rate": 0
        }

    runs = json.loads(path.read_text())

    total = len(runs)
    total_failed = sum(r.get("failed", 0) for r in runs)
    total_passed = sum(r.get("passed", 0) for r in runs)

    total_tests = total_passed + total_failed

    return {
        "total_runs": total,
        "total_passed": total_passed,
        "total_failed": total_failed,
        "average_pass_rate": (
            round((total_passed / total_tests) * 100, 2)
            if total_tests > 0 else 0
        )
    }


# -------------------------------------------------------
# Health Endpoint
# -------------------------------------------------------

@app.get("/api/health")
def health():
    return {
        "status": "healthy",
        "deployment": "AWS ECS Fargate",
        "version": "1.0.0",
        "timestamp": datetime.datetime.utcnow().isoformat()
    }
