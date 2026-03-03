"""
src/cognitive/agents/test_generator.py — LLM-powered test code generation.

Takes a test area (from strategy) + site model → generates Playwright/Pytest code
with proper assertions, waits, and fixtures.
"""
from __future__ import annotations

import json
from src.cognitive.agents.base_agent import BaseAgent, AgentContext, AgentResult

GENERATOR_SYSTEM_PROMPT = """You are a Senior QA Automation Engineer AI.

Generate production-quality Playwright + Pytest test code.

RULES:
1. Use pytest + playwright (sync API: page, browser, context fixtures)
2. Use proper waits: page.wait_for_selector(), expect(locator).to_be_visible()
3. Use data-testid selectors when available, fall back to role/text selectors
4. Include assertions for EVERY step (don't just click-and-hope)
5. Handle common failures: timeouts, element not found, stale selectors
6. Use pytest.mark.parametrize for data-driven tests where appropriate
7. Include docstrings explaining what each test verifies
8. Use page.goto() with the full URL from the site model
9. Group related tests in a class

OUTPUT FORMAT (JSON only):
{
  "area_id": "the area being tested",
  "test_file_name": "test_<area>.py",
  "test_count": <int>,
  "code": "<full Python test file content>",
  "fixtures_needed": ["page", "browser"],
  "dependencies": ["pytest", "playwright"],
  "notes": "any assumptions or caveats"
}
"""


class TestGeneratorAgent(BaseAgent):
    name = "test_generator"

    async def run(self, context: AgentContext) -> AgentResult:
        test_area = context.extra.get("test_area", {})
        if not test_area:
            return AgentResult(agent_name=self.name, status="error",
                               error="No test_area in context.extra")

        site_info = ""
        if context.site_model:
            sm = context.site_model
            relevant_pages = [
                p for p in sm.get("pages", [])
                if any(pg in p.get("url", "") for pg in test_area.get("pages", []))
            ]
            if relevant_pages:
                site_info = "RELEVANT PAGE DETAILS:\n"
                for p in relevant_pages[:5]:
                    site_info += f"""
Page: {p.get('url', '')}
  Type: {p.get('page_type', 'unknown')}
  Forms: {json.dumps(p.get('forms', []), indent=2)}
  Interactive elements: {json.dumps(p.get('interactive_elements', [])[:10], indent=2)}
  Links: {json.dumps([l.get('href', '') for l in p.get('links', [])[:10]])}
"""

        base_url = ""
        if context.target_url:
            base_url = context.target_url
        elif context.site_model:
            base_url = context.site_model.get("base_url", "")

        messages = [
            {"role": "system", "content": GENERATOR_SYSTEM_PROMPT},
            {"role": "user", "content": f"""
Generate test code for this test area:

AREA: {json.dumps(test_area, indent=2)}

BASE URL: {base_url}
ENVIRONMENT: {context.environment}

{site_info}

SPEC: {context.spec_text or 'No additional spec.'}

Generate comprehensive, production-ready test code.
"""}
        ]

        result = self._chat_json(messages, temperature=0.2, max_tokens=8192)

        if "error" in result and "code" not in result:
            return AgentResult(agent_name=self.name, status="error",
                               error=result.get("error", "Generation failed"))

        return AgentResult(agent_name=self.name, status="ok", data=result)

    async def generate_for_strategy(self, context: AgentContext,
                                     strategy: dict) -> list[AgentResult]:
        """Generate tests for ALL areas in a strategy."""
        results = []
        for area in strategy.get("test_areas", []):
            ctx = AgentContext(
                tenant_id=context.tenant_id,
                session_id=context.session_id,
                site_model=context.site_model,
                spec_text=context.spec_text,
                target_url=context.target_url,
                environment=context.environment,
                provider=context.provider,
                model=context.model,
                extra={"test_area": area},
            )
            result = await self.execute(ctx)
            results.append(result)
        return results
