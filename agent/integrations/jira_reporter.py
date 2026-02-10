import os
import requests
from requests.auth import HTTPBasicAuth

JIRA_URL = os.getenv("JIRA_URL")       # e.g. https://yourcompany.atlassian.net
JIRA_USER = os.getenv("JIRA_USER")
JIRA_TOKEN = os.getenv("JIRA_TOKEN")
JIRA_PROJECT = os.getenv("JIRA_PROJECT_KEY", "QA")

def create_jira_ticket(summary: str, description: str, issue_type="Bug"):
    url = f"{JIRA_URL}/rest/api/3/issue"
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    payload = {
        "fields": {
            "project": {"key": JIRA_PROJECT},
            "summary": summary,
            "description": description,
            "issuetype": {"name": issue_type}
        }
    }
    response = requests.post(
        url,
        headers=headers,
        auth=HTTPBasicAuth(JIRA_USER, JIRA_TOKEN),
        json=payload
    )
    if response.status_code == 201:
        return {"ticket": response.json().get("key"), "status": "created"}
    return {"status": "error", "details": response.text}
