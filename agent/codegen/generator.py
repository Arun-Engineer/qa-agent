from __future__ import annotations

import json
import re as re_mod
from pathlib import Path
from typing import Optional

from agent.utils.openai_wrapper import chat_completion
from agent.tools.selector_discovery import get_selectors_for_spec


class TestGenerator:
    def __init__(
        self,
        model: str = "gpt-4o-mini",
        prompt_template_path: str = "config/prompts/generate_test_file.md",
    ):
        self.model = model
        root = Path(__file__).resolve().parents[2]
        tpl_file = root / prompt_template_path
        if tpl_file.exists():
            self.prompt_template = tpl_file.read_text(encoding="utf-8")
        else:
            self.prompt_template = self._default_template()

    @staticmethod
    def _default_template() -> str:
        return """You are a Senior QA Automation Engineer generating Playwright + Pytest tests.

## ABSOLUTE RULES:
1. Use `from playwright.sync_api import Page, expect` — NEVER `from playwright.sync_api import Expect`
2. For URL assertions: `expect(page).to_have_url(re.compile(r".*pattern.*"))` — NEVER `expect.string_contains()`
3. For multiple elements: `.first` — e.g. `page.get_by_text("Required").first`
4. ALWAYS `import re` at top
5. After `page.goto()` → `page.wait_for_load_state("networkidle")`
6. After click that triggers validation → `page.wait_for_timeout(1000)`

## NEGATIVE TEST LOGIC:
When testing "user should NOT be able to login with invalid credentials":
- The TEST PASSES when the application correctly REJECTS the login
- The TEST FAILS only if there is a code/automation error
- A correctly rejected login = PASS, not FAIL

## STEP:
{{STEP}}

## USER SPEC:
{{SPEC}}

## SITE MODEL:
{{SITE_MODEL}}

## PRIOR ERROR TO FIX:
{{FIX_ERROR}}

Generate ONLY valid Python code. No markdown fences. No explanations."""

    def _read_site_model(self, site_model_path: Optional[str], max_chars: int = 12000) -> str:
        if not site_model_path:
            return ""
        try:
            p = Path(site_model_path)
            return p.read_text(encoding="utf-8")[:max_chars] if p.exists() else ""
        except Exception:
            return ""

    def generate_test_code(
        self,
        step: dict,
        spec: str = "",
        site_model_path: Optional[str] = None,
        fix_error: Optional[str] = None,
    ) -> str:
        prompt = self.prompt_template
        prompt = prompt.replace("{{STEP}}", json.dumps(step, ensure_ascii=False, indent=2))
        prompt = prompt.replace("{{SPEC}}", (spec or "").strip())
        prompt = prompt.replace("{{SITE_MODEL}}", self._read_site_model(site_model_path))
        prompt = prompt.replace("{{FIX_ERROR}}", (fix_error or "").strip())

        resp = chat_completion(
            model=self.model,
            messages=[
                {"role": "system", "content": (
                    "Return ONLY valid Python code. No markdown. No explanations.\n\n"
                    "CRITICAL PLAYWRIGHT RULES:\n"
                    "- NEVER use expect.string_contains() — it does NOT exist in Playwright Python\n"
                    "- For URL checks: expect(page).to_have_url(re.compile(r'.*pattern.*'))\n"
                    "- For multiple matching elements: use .first or .nth(0)\n"
                    "- Always import re at the top\n"
                    "- Always wait for networkidle after goto()\n"
                    "- After clicking submit, add page.wait_for_timeout(1500) before assertions\n"
                    "\n"
                    "NEGATIVE TEST LOGIC:\n"
                    "- When spec says 'user should NOT login', a successful rejection = PASS\n"
                    "- Test should verify the error message appears and URL stays on login page\n"
                    "- Do NOT mark test as failed when login is correctly rejected\n"
                )},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
            service_name="qa-agent-codegen",
        )

        code = (resp.choices[0].message.content or "").strip()

        # Strip markdown fences
        if code.startswith("```"):
            lines = code.split("\n")
            if lines[-1].strip() == "```":
                lines = lines[1:-1]
            elif lines[0].startswith("```"):
                lines = lines[1:]
            code = "\n".join(lines)

        if not code:
            raise RuntimeError("LLM returned empty test code")

        return self._post_process_code(code)

    @staticmethod
    def _post_process_code(code: str) -> str:
        """Auto-fix common Playwright mistakes LLMs make."""

        # Fix 1: expect.string_contains("x") → re.compile(r".*x.*")
        code = re_mod.sub(
            r'expect\.string_contains\((["\'])(.*?)\1\)',
            r're.compile(r".*\2.*")',
            code,
        )

        # Fix 2: Ensure 'import re' if re.compile is used
        if "re.compile" in code and "import re" not in code:
            code = "import re\n" + code

        # Fix 3: page.locator("text=X") → page.get_by_text("X").first
        code = re_mod.sub(
            r'page\.locator\("text=([^"]+)"\)(?!\.first|\.nth)',
            r'page.get_by_text("\1").first',
            code,
        )

        # Fix 4: expect(page.locator("text=X")) → expect(page.get_by_text("X").first)
        code = re_mod.sub(
            r'expect\(page\.locator\("text=([^"]+)"\)\)',
            r'expect(page.get_by_text("\1").first)',
            code,
        )

        return code

    def write_test_file(self, code: str, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(code, encoding="utf-8")
        return path