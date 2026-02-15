# agent/planner.py

import json
from openai import OpenAI
from pathlib import Path

# agent/planner.py
import json
from pathlib import Path
from openai import OpenAI


class Planner:
    def __init__(self, model: str = "gpt-4o-mini", prompt_path: str = "config/prompts/generate_tests.md"):
        self.model = model
        self.prompt = Path(prompt_path).read_text(encoding="utf-8")
        self.client = OpenAI()  # uses OPENAI_API_KEY env var

    def generate_plan(self, spec: str) -> dict:
        messages = [
            {"role": "system", "content": self.prompt},
            {"role": "user", "content": spec},
        ]

        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=0.2,
                response_format={"type": "json_object"},
            )
            content = (resp.choices[0].message.content or "").strip()
            return json.loads(content)
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

    import re, os
    from agent.tools import ui_recon_runner

    spec = args.spec  # ✅ define it first

    url_match = re.search(r"https?://[^\s)]+", spec)
    if url_match:
        base_url = url_match.group(0).rstrip(".,;").rstrip("/")
        os.environ["BASE_URL"] = base_url
        os.environ["APP_BASE_URL"] = base_url

        recon = ui_recon_runner.run_recon(base_url=base_url, max_pages=25, max_depth=2)
        if recon.get("status") == "ok":
            os.environ["SITE_MODEL_PATH"] = recon["model_path"]
            spec = spec + "\n\n" + recon["summary"] + "\n"

    planner = Planner()
    plan = planner.generate_plan(spec)  # ✅ NOT args.spec

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