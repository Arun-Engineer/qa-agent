import re
from agent.jira_connector import AzureDevOpsClient
from agent.github_fetcher import GitHubClient

from pathlib import Path
import json

def load_integrations():
    config_path = Path("config/integrations.json")
    return json.loads(config_path.read_text())

def fetch_ticket(ticket_id_or_url):
    cfg = load_integrations()

    # Azure detection
    azure_match = re.search(r"(\d{5,})$", ticket_id_or_url)
    if "azure" in cfg and azure_match:
        azure = cfg["azure"]
        client = AzureDevOpsClient(azure["base_url"], azure["project"], azure["token"])
        return client.fetch_work_item(azure_match.group(1))

    # GitHub
    gh_match = re.match(r"gh-issue:(\d+)", ticket_id_or_url)
    if "github" in cfg and gh_match:
        client = GitHubClient(cfg["github"]["repo"], cfg["github"]["token"])
        return client.fetch_issue(gh_match.group(1))

    raise ValueError("Unable to route ticket ID or URL")
