# agent/planner.py

import json
from openai import OpenAI
from pathlib import Path

class Planner:

    def __init__(
            self,
            model="gpt-4o-mini",
            prompt_path="config/prompts/generate_tests.md"
    ):
        path = Path(prompt_path)
        if not path.exists():
            raise FileNotFoundError(f"Missing prompt file: {path}")
        self.prompt = path.read_text(encoding="utf-8")
        self.model = model
        self.client = OpenAI()

    def generate_plan(self, spec: str) -> dict:
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": self.prompt},
                    {"role": "user", "content": spec}
                ],
                response_format={"type": "json_object"}
            )
            return json.loads(response.choices[0].message.content)
        except Exception as e:
            return {"error": str(e)}

    def _parse_response(self, response) -> dict:
        content = response.content.strip()
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return {"error": "Invalid JSON from LLM", "raw": content}

if __name__ == "__main__":
    import argparse
    from agent.tools import pytest_runner, playwright_runner, api_caller, bug_reporter

    parser = argparse.ArgumentParser()
    parser.add_argument("--spec", type=str, required=True, help="Spec describing the QA task")
    args = parser.parse_args()

    planner = Planner()
    plan = planner.generate_plan(args.spec)

    if "steps" not in plan:
        print("Planner Error:", plan)
        exit(1)

    for step in plan["steps"]:
        tool_name = step["tool"]
        tool_args = step["args"]

        if tool_name == "pytest_runner":
            output = pytest_runner.run_pytest(**tool_args)
        elif tool_name == "playwright_runner":
            output = playwright_runner.run_playwright(**tool_args)
        elif tool_name == "api_caller":
            output = api_caller.call_api(**tool_args)
        elif tool_name == "bug_reporter":
            output = bug_reporter.file_bug(**tool_args)
        else:
            output = f"[SKIP] Tool {tool_name} not yet wired."

        print(f"[STEP] {tool_name} with args={tool_args}\n[RESULT]\n{output}\n")


# Example structure expected from LLM JSON output
# {
#   "goal": "Test user login feature",
#   "steps": [
#     {"tool": "pytest_runner", "args": {"path": "tests/test_login.py"}},
#     {"tool": "bug_reporter", "args": {"error_code": 401, "module": "auth"}}
#   ]
# }