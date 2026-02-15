from fastapi import FastAPI
import subprocess

app = FastAPI()

@app.get("/")
def health():
    return {"status": "QA Agent Running"}

@app.post("/run")
def run_agent(spec: str):
    result = subprocess.run(
        ["python", "main.py", "--spec", spec],
        capture_output=True,
        text=True
    )
    return {"output": result.stdout}
