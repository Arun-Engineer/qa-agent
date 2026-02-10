import requests
import json

class AzureDevOpsClient:
    def __init__(self, base_url, project, token):
        self.base_url = base_url.rstrip('/')
        self.project = project
        self.token = token
        self.headers = {
            "Content-Type": "application/json",
            "Authorization": f"Basic {self._get_basic_token()}"
        }

    def _get_basic_token(self):
        import base64
        return base64.b64encode(f":{self.token}".encode()).decode()

    def fetch_work_item(self, work_item_id):
        url = f"{self.base_url}/{self.project}/_apis/wit/workitems/{work_item_id}?api-version=7.0"
        response = requests.get(url, headers=self.headers)
        response.raise_for_status()
        data = response.json()
        return {
            "title": data['fields'].get('System.Title'),
            "description": data['fields'].get('System.Description'),
            "repro_steps": data['fields'].get('Microsoft.VSTS.TCM.ReproSteps', ''),
            "severity": data['fields'].get('Microsoft.VSTS.Common.Severity', 'Medium'),
            "priority": data['fields'].get('Microsoft.VSTS.Common.Priority', 2)
        }
