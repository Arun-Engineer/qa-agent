import json
from pathlib import Path
from agent.bug_adapters.github_reporter import file_github_bug
from agent.bug_adapters.azure_reporter import file_azure_bug
from agent.bug_adapters.jira_reporter import file_jira_bug

def load_config():
    return json.loads(Path("config/integrations.json").read_text())

def file_bug(title, severity, details, steps_to_reproduce=None):
    cfg = load_config()
    steps = steps_to_reproduce or []

    body = f"Severity: {severity}\n\nDetails:\n{details}\n\nSteps to Reproduce:\n" + "\n".join(steps)

    if "github" in cfg:
        gh = cfg["github"]
        return file_github_bug(gh["token"], gh["repo"], title, body)

    if "azure" in cfg:
        az = cfg["azure"]
        return file_azure_bug(
            az["token"], az["base_url"], az["project"],
            title, details, "\n".join(steps)
        )

    if "jira" in cfg:
        jira = cfg["jira"]
        return file_jira_bug(jira["base_url"], jira["token"], jira["project_key"], title, body)

    return {"error": "No bug platform configured."}
