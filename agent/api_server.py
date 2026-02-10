# api_server.py
from fastapi import FastAPI, UploadFile
from pydantic import BaseModel
from agent.main import run_agent_from_spec
import uvicorn

app = FastAPI()

class SpecRequest(BaseModel):
    spec: str
    html: bool = False
    trace: bool = False

@app.post("/run")
def run_test(req: SpecRequest):
    result = run_agent_from_spec(req.spec, html=req.html, trace=req.trace)
    return {"status": "completed", "result": result}

@app.get("/")
def root():
    return {"message": "QA Agent is up"}

if __name__ == "__main__":
    uvicorn.run("api_server:app", host="0.0.0.0", port=8000)
