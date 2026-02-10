import requests

def file_jira_bug(base_url, token, project_key, title, description):
    url = f"{base_url}/rest/api/2/issue"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    payload = {
        "fields": {
            "project": {"key": project_key},
            "summary": title,
            "description": description,
            "issuetype": {"name": "Bug"}
        }
    }
    response = requests.post(url, headers=headers, json=payload)
    response.raise_for_status()
    return {"url": f"{base_url}/browse/{response.json()['key']}", "id": response.json()["key"]}
