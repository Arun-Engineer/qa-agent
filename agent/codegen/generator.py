from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from openai import OpenAI
from agent.utils.openai_wrapper import chat_completion  # NEW

class TestGenerator:
    def __init__(
        self,
        model: str = "gpt-4o-mini",
        prompt_template_path: str = "config/prompts/generate_test_file.md",
    ):
        self.model = model

        # Make path independent of current working dir
        root = Path(__file__).resolve().parents[2]  # agent/codegen/generator.py -> project root
        self.prompt_template_path = prompt_template_path
        self.prompt_template = (root / prompt_template_path).read_text(encoding="utf-8")

    def _read_site_model(self, site_model_path: Optional[str], max_chars: int = 12000) -> str:
        if not site_model_path:
            return ""
        try:
            p = Path(site_model_path)
            if not p.exists():
                return ""
            return p.read_text(encoding="utf-8")[:max_chars]
        except Exception:
            return ""

    def generate_test_code(
        self,
        step: dict,
        spec: str = "",
        site_model_path: Optional[str] = None,
        fix_error: Optional[str] = None,
    ) -> str:
        """
        step: planner step dict
        spec: full user spec (optionally enriched)
        site_model_path: path returned by ui_recon_runner (optional)
        fix_error: prior failure text to help regenerate (optional)
        """
        prompt = self.prompt_template

        # REQUIRED placeholders
        prompt = prompt.replace("{{STEP}}", json.dumps(step, ensure_ascii=False, indent=2))
        prompt = prompt.replace("{{SPEC}}", (spec or "").strip())
        prompt = prompt.replace("{{SITE_MODEL}}", self._read_site_model(site_model_path))
        prompt = prompt.replace("{{FIX_ERROR}}", (fix_error or "").strip())

        resp = self.chat_completions(
            model=self.model,
            messages=[
                {"role": "system", "content": "Return ONLY valid Python code. No markdown. No explanations."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
            service_name="qa-agent-codegen",
        )

        code = (resp.choices[0].message.content or "").strip()
        if not code:
            raise RuntimeError("LLM returned empty test code")
        return code

    def write_test_file(self, code: str, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(code, encoding="utf-8")
        return path
