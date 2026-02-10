# tests/unit/test_planner.py
import pytest
from agent.planner import Planner

class MockLLM:
    def chat(self):
        return self

    def completions(self):
        return self

    def create(self, messages, response_format):
        return type("Resp", (), {
            "choices": [
                type("Msg", (), {"message": type("M", (), {"content": '{"goal": "Test login", "steps": []}'})()
                })
            ]
        })()

@pytest.fixture
def planner():
    p = Planner()
    p.llm = MockLLM()
    return p

def test_generate_plan_success(planner):
    plan = planner.generate_plan("Login test")
    assert isinstance(plan, dict)
    assert plan["goal"] == "Test login"





