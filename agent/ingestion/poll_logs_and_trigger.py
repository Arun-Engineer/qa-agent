# === poll_logs_and_trigger.py ===
# Monitors logs/errors.json and triggers QA agent

import json
import time
from main import run_agent_from_spec

LOG_PATH = "data/logs/errors.json"
last_seen = None

while True:
    try:
        with open(LOG_PATH) as f:
            errors = json.load(f)
            if errors != last_seen:
                last_seen = errors
                for err in errors:
                    spec = f"Investigate: {err['error']} at {err['path']}"
                    run_agent_from_spec(spec, html=True)
    except Exception as e:
        print(f"Log polling failed: {e}")
    time.sleep(30)