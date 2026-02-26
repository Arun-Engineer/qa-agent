from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from pydantic import BaseModel
from pathlib import Path
import json
import datetime
import os

from agent.agent_runner import run_agent_from_spec, explain_mode
from auth.db import Base, engine
from auth.routes import router as auth_router
from agent.utils.openai_wrapper import get_client

app = FastAPI(title="AI QA Orchestration Platform")

# sessions for login cookie
app.add_middleware(
    SessionMiddleware,
    secret_key=os.getenv("SESSION_SECRET", "dev-only-change-me"),
)

# create user table
Base.metadata.create_all(bind=engine)

# register /login /signup /dashboard routes
app.include_router(auth_router)

# initialize OpenAI wrapper (if your project expects this)
get_client(service_name="AI QA Orchestration Platform")

# artifacts dir (FIX: was missing before)
ARTIFACT_DIR = Path(os.getenv("ARTIFACT_DIR", "artifacts")).resolve()
ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)

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
# Static UI Mount
# -------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
UI_DIR = BASE_DIR / "ui"
if UI_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(UI_DIR)), name="static")

@app.get("/", include_in_schema=False)
def root(request: Request):
    if request.session.get("user_id"):
        return RedirectResponse(url="/dashboard", status_code=303)
    return RedirectResponse(url="/login", status_code=303)

# -------------------------------------------------------
# Execute Spec Endpoint
# -------------------------------------------------------
@app.post("/api/spec")
def run_spec(spec_req: SpecRequest):
    try:
        return run_agent_from_spec(spec_req.spec, html=spec_req.html, trace=spec_req.trace)
    except Exception as e:
        return {
            "status": "failed",
            "error": str(e),
            "timestamp": datetime.datetime.utcnow().isoformat(),
        }

@app.post("/api/run")
def run(req: SpecRequest):
    return run_agent_from_spec(req.spec, html=req.html, trace=req.trace)

@app.post("/api/explain")
def explain(req: ExplainRequest):
    return {"answer": explain_mode(req.question)}

@app.get("/api/runs")
def get_runs():
    path = Path("data/runs.json")
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return []

@app.get("/api/metrics")
def metrics():
    path = Path("data/runs.json")
    if not path.exists():
        return {
            "total_runs": 0,
            "unique_suites": 0,
            "total_passed": 0,
            "total_failed": 0,
            "average_pass_rate": 0,
            "lifetime_total_passed": 0,
            "lifetime_total_failed": 0,
            "lifetime_pass_rate": 0,
        }

    try:
        runs = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(runs, list):
            runs = []
    except Exception:
        runs = []

    def _parse_ts(ts: str):
        if not ts:
            return datetime.datetime.min
        try:
            return datetime.datetime.fromisoformat(ts)
        except Exception:
            return datetime.datetime.min

    total_runs = len(runs)
    lifetime_failed = sum(int(r.get("failed", 0) or 0) for r in runs)
    lifetime_passed = sum(int(r.get("passed", 0) or 0) for r in runs)
    lifetime_total_tests = lifetime_passed + lifetime_failed
    lifetime_pass_rate = round((lifetime_passed / lifetime_total_tests) * 100, 2) if lifetime_total_tests > 0 else 0

    latest_by_goal = {}
    for r in runs:
        goal = (r.get("goal") or "").strip()
        if not goal:
            continue
        key = goal.lower()
        ts = _parse_ts(r.get("timestamp"))
        prev = latest_by_goal.get(key)
        if not prev or ts > _parse_ts(prev.get("timestamp")):
            latest_by_goal[key] = r

    unique_suites = len(latest_by_goal)
    current_failed = sum(int(r.get("failed", 0) or 0) for r in latest_by_goal.values())
    current_passed = sum(int(r.get("passed", 0) or 0) for r in latest_by_goal.values())
    current_total_tests = current_passed + current_failed
    current_pass_rate = round((current_passed / current_total_tests) * 100, 2) if current_total_tests > 0 else 0

    return {
        "total_runs": total_runs,
        "unique_suites": unique_suites,
        "total_passed": current_passed,
        "total_failed": current_failed,
        "average_pass_rate": current_pass_rate,
        "lifetime_total_passed": lifetime_passed,
        "lifetime_total_failed": lifetime_failed,
        "lifetime_pass_rate": lifetime_pass_rate,
    }

@app.get("/api/health")
def health():
    return {
        "status": "healthy",
        "deployment": "AWS ECS Fargate",
        "version": "1.0.0",
        "timestamp": datetime.datetime.utcnow().isoformat(),
    }

@app.get("/api/artifacts/{filename}")
def download_artifact(filename: str):
    safe_name = Path(filename).name
    path = (ARTIFACT_DIR / safe_name).resolve()

    if ARTIFACT_DIR not in path.parents and path != ARTIFACT_DIR:
        raise HTTPException(status_code=400, detail="Invalid filename")

    if not path.exists():
        raise HTTPException(status_code=404, detail="Not Found")

    suffix = path.suffix.lower()
    media = "application/octet-stream"
    if suffix == ".pdf":
        media = "application/pdf"
    elif suffix == ".xlsx":
        media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    elif suffix == ".json":
        media = "application/json"

    return FileResponse(path, media_type=media, filename=safe_name)