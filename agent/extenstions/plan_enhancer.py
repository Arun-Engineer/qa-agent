# agent/extensions/plan_enhancer.py
import json
from pathlib import Path
from agent.extensions.vector_memory import BugMemory
from agent.extensions.test_prioritizer import prioritize_test_plan

memory = BugMemory()

def load_past_failures(log_dir="data/logs"):
    log_path = Path(log_dir)
    for log_file in log_path.glob("run_*.json"):
        try:
            log_data = json.loads(log_file.read_text())
            for entry in log_data.get("results", []):
                if "failed" in json.dumps(entry).lower():
                    memory.add_bug(json.dumps(entry), {"source": log_file.name})
        except Exception:
            continue

def enhance_plan(plan: dict):
    # prioritize first
    plan = prioritize_test_plan(plan)

    # then enrich with memory tags
    for step in plan.get("steps", []):
        text = json.dumps(step)
        similar = memory.find_similar(text, top_k=2)
        if similar:
            step["similar_issues"] = similar
    return plan
