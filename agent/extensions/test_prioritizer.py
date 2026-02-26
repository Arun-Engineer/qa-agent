# agent/extensions/test_prioritizer.py
from collections import defaultdict

def prioritize_test_plan(plan: dict):
    critical_keywords = ["payment", "auth", "checkout", "data loss", "P0", "security"]
    score_map = defaultdict(int)
    for i, step in enumerate(plan.get("steps", [])):
        text = json.dumps(step)
        score = sum(k in text.lower() for k in critical_keywords)
        score_map[i] = score

    sorted_steps = sorted(plan["steps"], key=lambda s: -score_map[plan["steps"].index(s)])
    plan["steps"] = sorted_steps
    return plan
