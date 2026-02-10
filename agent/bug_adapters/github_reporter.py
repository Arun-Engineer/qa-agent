import requests

def file_github_bug(token, repo, title, body):
    url = f"https://api.github.com/repos/{repo}/issues"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json"
    }
    payload = {
        "title": title,
        "body": body
    }
    response = requests.post(url, headers=headers, json=payload)
    response.raise_for_status()
    return {"url": response.json()["html_url"], "id": response.json()["number"]}
