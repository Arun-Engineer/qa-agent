"""
src/cognitive/agents/failure_triage.py — 5-way failure classification.

Categories: Bug | Flaky | Environment | Data | Stale Selector
Uses vector similarity if available, otherwise pure LLM classification.
"""
from __future__ import annotations

import json
from src.cognitive.agents.base_agent import BaseAgent, AgentContext, AgentResult

TRIAGE_SYSTEM_PROMPT = """You are a QA Failure Triage AI. Classify test failures.

For each failure, determine the root cause category and confidence.

CATEGORIES:
- BUG: Real application defect. The test found a genuine problem.
- FLAKY: Intermittent failure due to timing, race conditions, or non-determinism.
- ENVIRONMENT: Infrastructure issue (server down, network, deploy in progress).
- DATA: Test data issue (missing fixture data, stale DB, changed seed data).
- STALE_SELECTOR: The test's selectors no longer match the DOM.

OUTPUT FORMAT (JSON only):
{
  "triaged_failures": [
    {
      "test_name": "test_xxx",
      "category": "BUG",
      "confidence": 0.92,
      "reasoning": "The assertion error shows price calculation is wrong",
      "suggested_action": "File bug: price calc off by 1 cent on discount",
      "severity": "high"
    }
  ],
  "summary": {
    "total": <int>,
    "bugs": <int>,
    "flaky": <int>,
    "environment": <int>,
    "data": <int>,
    "stale_selector": <int>
  }
}

RULES:
- confidence: 0.0 to 1.0
- severity: "critical" | "high" | "medium" | "low"
- Be specific in reasoning — quote the actual error
- If uncertain, lean toward BUG (better to investigate than ignore)
"""


class FailureTriageAgent(BaseAgent):
    name = "failure_triage"

    async def run(self, context: AgentContext) -> AgentResult:
        failures = context.extra.get("failures", [])
        if not failures:
            return AgentResult(agent_name=self.name, status="ok",
                               data={"triaged_failures": [], "summary": {
                                   "total": 0, "bugs": 0, "flaky": 0,
                                   "environment": 0, "data": 0, "stale_selector": 0}})

        failure_text = ""
        for i, f in enumerate(failures[:20]):  # cap at 20
            failure_text += f"""
--- Failure {i+1} ---
Test: {f.get('test_name', 'unknown')}
Error: {f.get('error_message', 'no message')}
Traceback: {f.get('traceback', 'none')[:500]}
Duration: {f.get('duration_ms', '?')}ms
Retry count: {f.get('retry_count', 0)}
"""

        messages = [
            {"role": "system", "content": TRIAGE_SYSTEM_PROMPT},
            {"role": "user", "content": f"""
Triage these test failures:

{failure_text}

ENVIRONMENT: {context.environment}
SITE: {context.target_url or 'unknown'}
"""}
        ]

        result = self._chat_json(messages, temperature=0.1, max_tokens=2048)

        if "error" in result and "triaged_failures" not in result:
            return AgentResult(agent_name=self.name, status="error",
                               error=result.get("error", "Triage failed"))

        return AgentResult(agent_name=self.name, status="ok", data=result)
