import os
import requests

SLACK_WEBHOOK = os.getenv("SLACK_WEBHOOK_URL")

def send_slack_alert(message: str):
    if not SLACK_WEBHOOK:
        return {"status": "disabled", "message": message}
    response = requests.post(SLACK_WEBHOOK, json={"text": message})
    return {"status": response.status_code, "text": response.text}
