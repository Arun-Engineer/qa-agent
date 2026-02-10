# main_api.py
from fastapi import FastAPI, Request
from pydantic import BaseModel
from main import run_agent_from_spec

app = FastAPI()

class SpecRequest(BaseModel):
    spec: str
    html: bool = False
    trace: bool = False

@app.post("/run")
def run_test(spec_req: SpecRequest):
    run_agent_from_spec(spec_req.spec, html=spec_req.html, trace=spec_req.trace)
    return {"status": "ok", "message": "Run triggered"}
