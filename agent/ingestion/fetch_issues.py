# === fetch_issues.py ===
# Auto-pulls issues from GitHub or Jira and triggers agent run

import requests
from main import run_agent_from_spec

GITHUB_REPO = "your_org/your_repo"
GITHUB_TOKEN = "your_token_here"

headers = {"Authorization": f"Bearer {GITHUB_TOKEN}"}

resp = requests.get(f"https://api.github.com/repos/{GITHUB_REPO}/issues", headers=headers)
issues = resp.json()

for issue in issues:
    if "bug" in issue.get("title", "").lower():
        spec = f"Reproduce and verify: {issue['title']}\nDetails: {issue['body']}"
        run_agent_from_spec(spec, html=True)


