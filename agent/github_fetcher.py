import requests

class GitHubClient:
    def __init__(self, repo, token):
        self.repo = repo
        self.token = token
        self.headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.token}"
        }

    def fetch_issue(self, issue_number):
        url = f"https://api.github.com/repos/{self.repo}/issues/{issue_number}"
        r = requests.get(url, headers=self.headers)
        r.raise_for_status()
        data = r.json()
        return {
            "title": data["title"],
            "description": data.get("body", "")
        }
