from fastapi import FastAPI, Query
from pydantic import BaseModel
from agent.main import run_agent_from_spec

app = FastAPI()

class SpecRequest(BaseModel):
    spec: str
    html: bool = False
    trace: bool = False

@app.post("/run")
def run_spec(req: SpecRequest):
    run_agent_from_spec(spec=req.spec, html=req.html, trace=req.trace)
    return {"status": "completed"}
