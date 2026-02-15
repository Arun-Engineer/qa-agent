import pytest
import requests
from jsonschema import validate
from tenacity import retry, stop_after_attempt, wait_fixed

schema = {
    "type": "object",
    "properties": {
        "message": {"type": "string"},
        "status": {"type": "string"}
    },
    "required": ["message", "status"]
}

@pytest.fixture(scope="module")
def base_url():
    return "https://api.example.com"

@pytest.fixture(scope="module")
def auth_token():
    payload = {"username": "test_user", "password": "test_pass"}
    response = requests.post(f"{base_url()}/auth/login", json=payload)
    assert response.status_code == 200
    return response.json().get("token")

@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def make_request(url, headers=None):
    return requests.get(url, headers=headers, timeout=10)

@pytest.mark.api
@pytest.mark.p1
@pytest.mark.positive
@pytest.mark.parametrize("endpoint, expected", [
    ("/data/1", {"message": "Success", "status": "200"}),
    ("/data/2", {"message": "Success", "status": "200"})
])
def test_get_data_api(endpoint, expected, auth_token):
    url = f"{base_url()}{endpoint}"
    headers = {"Authorization": f"Bearer {auth_token}"}
    response = make_request(url, headers=headers)
    
    assert response.status_code == 200
    try:
        body = response.json()
        validate(instance=body, schema=schema)
        assert body == expected
    except Exception as e:
        assert response.text.strip() != ""
        raise e