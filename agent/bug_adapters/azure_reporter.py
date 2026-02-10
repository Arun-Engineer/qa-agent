import requests
import base64

def file_azure_bug(token, org_url, project, title, description, repro_steps):
    url = f"{org_url}/{project}/_apis/wit/workitems/$Bug?api-version=7.0"
    headers = {
        "Content-Type": "application/json-patch+json",
        "Authorization": f"Basic {base64.b64encode(f':{token}'.encode()).decode()}"
    }
    payload = [
        {"op": "add", "path": "/fields/System.Title", "value": title},
        {"op": "add", "path": "/fields/System.Description", "value": description},
        {"op": "add", "path": "/fields/Microsoft.VSTS.TCM.ReproSteps", "value": repro_steps}
    ]
    response = requests.post(url, headers=headers, json=payload)
    response.raise_for_status()
    return {"url": response.json()["url"], "id": response.json()["id"]}
