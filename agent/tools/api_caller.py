# agent/tools/api_caller.py
import requests

def call_api(method: str, url: str, headers=None, body=None):
    try:
        response = requests.request(method=method.upper(), url=url, headers=headers or {}, json=body)
        return {
            "status": response.status_code,
            "body": response.json() if 'application/json' in response.headers.get('content-type', '') else response.text,
            "ok": response.ok
        }
    except requests.exceptions.RequestException as e:
        return {"status": "error", "error": str(e)}