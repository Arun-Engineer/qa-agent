"""
src/cognitive/agents/self_healer.py — Auto-fix broken tests.

Takes a failed test + current DOM → suggests/applies fixes for:
  - Stale selectors (element changed ID/class/position)
  - Missing waits (element not yet rendered)
  - Wrong assertions (expected value changed)
"""
from __future__ import annotations

import json
from src.cognitive.agents.base_agent import BaseAgent, AgentContext, AgentResult

HEALER_SYSTEM_PROMPT = """You are a QA Self-Healing AI. Fix broken test code.

Given a failed test's code, error, and the current page DOM, produce a fixed version.

OUTPUT FORMAT (JSON only):
{
  "original_test": "test function name",
  "fix_type": "selector_update" | "wait_added" | "assertion_updated" | "flow_changed" | "multiple",
  "changes_summary": "Changed #login-btn to [data-testid=login-submit] + added wait",
  "fixed_code": "<complete fixed test function code>",
  "confidence": 0.85,
  "requires_review": false,
  "diff_lines": [
    {"line": 12, "old": "page.click('#login-btn')", "new": "page.click('[data-testid=login-submit]')"}
  ]
}

RULES:
- Prefer data-testid selectors over fragile CSS/XPath
- Always add explicit waits before interactions
- Use expect() assertions over raw assert
- Keep the test's INTENT identical — only fix the HOW
- If fix is uncertain, set requires_review=true
"""


class SelfHealerAgent(BaseAgent):
    name = "self_healer"

    async def run(self, context: AgentContext) -> AgentResult:
        failed_test = context.extra.get("failed_test", {})
        if not failed_test:
            return AgentResult(agent_name=self.name, status="error",
                               error="No failed_test in context.extra")

        dom_snapshot = context.extra.get("dom_snapshot", "")
        if dom_snapshot and len(dom_snapshot) > 5000:
            dom_snapshot = dom_snapshot[:5000] + "\n... (truncated)"

        messages = [
            {"role": "system", "content": HEALER_SYSTEM_PROMPT},
            {"role": "user", "content": f"""
Fix this broken test:

TEST NAME: {failed_test.get('test_name', 'unknown')}
TEST CODE:
```python
{failed_test.get('code', 'not available')}
```

ERROR:
{failed_test.get('error_message', 'unknown error')}

TRACEBACK:
{(failed_test.get('traceback', 'none'))[:1000]}

CURRENT PAGE DOM (simplified):
{dom_snapshot or 'Not available — fix based on error context only.'}

PAGE URL: {failed_test.get('url', context.target_url or 'unknown')}
"""}
        ]

        result = self._chat_json(messages, temperature=0.1)

        if "error" in result and "fixed_code" not in result:
            return AgentResult(agent_name=self.name, status="error",
                               error=result.get("error", "Healing failed"))

        return AgentResult(agent_name=self.name, status="ok", data=result)
