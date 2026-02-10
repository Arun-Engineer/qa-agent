# agent/memory.py
import json
import datetime
from pathlib import Path

def log_run_result(output: dict, base="data/logs"):
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    path = Path(base)
    path.mkdir(parents=True, exist_ok=True)
    file = path / f"run_{ts}.json"
    file.write_text(json.dumps(output, indent=2))
    return str(file)

def save_artifact(name: str, data: bytes, ext=".png", base="data/artifacts"):
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    path = Path(base)
    path.mkdir(parents=True, exist_ok=True)
    file = path / f"{ts}_{name}{ext}"
    file.write_bytes(data)
    return str(file)

def load_recent_logs(limit=10, base="data/logs"):
    path = Path(base)
    files = sorted(path.glob("run_*.json"), reverse=True)[:limit]
    logs = []
    for f in files:
        try:
            logs.append(json.loads(f.read_text()))
        except Exception:
            continue
    return logs
