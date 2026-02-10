# agent/tools/bug_reporter.py
def file_bug(error_code: int, module: str, summary: str = "Auto-filed bug", logs: str = None):
    ticket_id = f"BUG-{module.upper()}-{error_code}"
    # In production, integrate with Jira or GitHub Issues here.
    return {
        "ticket": ticket_id,
        "summary": summary,
        "module": module,
        "logs": logs or "Attached test logs",
        "status": "created"
    }