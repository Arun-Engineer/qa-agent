# === slack_trigger.py ===
# Slack slash command to run the agent

from fastapi import FastAPI, Request
from main import run_agent_from_spec

app = FastAPI()

@app.post("/slack/trigger-test")
async def trigger_test(request: Request):
    form = await request.form()
    spec = form.get("text", "Test login flow")
    run_agent_from_spec(spec, html=True)
    return {"response_type": "in_channel", "text": "✅ QA Agent triggered for spec: " + spec}
