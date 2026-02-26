import os
import requests
import pytest
from requests.exceptions import RequestException

pytestmark = pytest.mark.parametrize("case", [
    {
        "name": "invalid_username_invalid_password",
        "inputs": {
            "username": "invalidUser",
            "password": "invalidPass"
        },
        "expected": {
            "status_code": 401,
            "error_any_of": ["Invalid username or password"]
        }
    },
    {
        "name": "empty_username",
        "inputs": {
            "username": "",
            "password": "validPass"
        },
        "expected": {
            "status_code": 400,
            "error_any_of": ["Username is required"]
        }
    },
    {
        "name": "empty_password",
        "inputs": {
            "username": "validUser",
            "password": ""
        },
        "expected": {
            "status_code": 400,
            "error_any_of": ["Password is required"]
        }
    },
    {
        "name": "whitespace_username",
        "inputs": {
            "username": "   ",
            "password": "validPass"
        },
        "expected": {
            "status_code": 400,
            "error_any_of": ["Invalid username or password"]
        }
    },
    {
        "name": "whitespace_password",
        "inputs": {
            "username": "validUser",
            "password": "   "
        },
        "expected": {
            "status_code": 400,
            "error_any_of": ["Invalid username or password"]
        }
    },
    {
        "name": "long_username",
        "inputs": {
            "username": "a",
            "password": "validPass"
        },
        "expected": {
            "status_code": 400,
            "error_any_of": ["Invalid username or password"]
        }
    },
    {
        "name": "long_password",
        "inputs": {
            "username": "validUser",
            "password": "a"
        },
        "expected": {
            "status_code": 400,
            "error_any_of": ["Invalid username or password"]
        }
    },
    {
        "name": "special_chars_username",
        "inputs": {
            "username": "!@#$%^&*()",
            "password": "validPass"
        },
        "expected": {
            "status_code": 400,
            "error_any_of": ["Invalid username or password"]
        }
    }
])

BASE_URL = os.getenv("API_BASE_URL", "https://example.com").rstrip('/')

def test_login_failure(case):
    url = f"{BASE_URL}/login"
    response = requests.post(url, json=case["inputs"])
    
    assert response.status_code == case["expected"]["status_code"]
    for error in case["expected"]["error_any_of"]:
        assert error in response.text

    if response.status_code != 200:
        print(f"Error for case {case['name']}: {response.text}")