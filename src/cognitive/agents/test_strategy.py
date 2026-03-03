"""
src/cognitive/agents/test_strategy.py — Risk-based test planning.

Takes site model (Phase 2) + spec text → produces a test strategy:
  - Which modules/pages to test
  - Test depth per module (smoke / regression / deep)
  - Test techniques (UI, API, visual, a11y)
  - Priority order based on risk
"""
from __future__ import annotations

import json
from src.cognitive.agents.base_agent import BaseAgent, AgentContext, AgentResult

STRATEGY_SYSTEM_PROMPT = """You are a Senior QA Test Strategist AI.

Given a site model (pages, forms, APIs discovered) and optional spec/requirements,
produce a risk-based test strategy.

OUTPUT FORMAT (JSON only):
{
  "strategy_name": "descriptive name",
  "total_test_areas": <int>,
  "test_areas": [
    {
      "area_id": "area_001",
      "name": "Login Flow",
      "pages": ["/login"],
      "risk_level": "high",
      "depth": "deep",
      "techniques": ["ui_functional", "api_validation", "security"],
      "test_cases_estimate": 8,
      "rationale": "Authentication is critical path"
    }
  ],
  "execution_order": ["area_001", "area_002"],
  "estimated_total_cases": <int>,
  "notes": "any strategic observations"
}

RULES:
- risk_level: "critical" | "high" | "medium" | "low"
- depth: "smoke" | "regression" | "deep" | "exploratory"
- techniques: pick from ["ui_functional", "ui_visual", "api_validation",
  "security", "accessibility", "performance", "data_validation",
  "error_handling", "boundary_testing", "cross_browser"]
- Order by risk (critical first)
- Be practical — don't generate 100 areas for a simple app
"""


class TestStrategyAgent(BaseAgent):
    name = "test_strategy"

    async def run(self, context: AgentContext) -> AgentResult:
        site_summary = ""
        if context.site_model:
            sm = context.site_model
            pages = sm.get("pages", [])
            page_list = "\n".join([
                f"  - {p.get('url', '?')} [{p.get('page_type', '?')}] "
                f"forms={len(p.get('forms', []))} links={len(p.get('links', []))}"
                for p in pages[:50]
            ])
            api_endpoints = sm.get("api_endpoints", [])
            api_list = "\n".join([
                f"  - {a.get('method', '?')} {a.get('url', '?')}"
                for a in api_endpoints[:30]
            ])
            auth_info = sm.get("auth", {})

            site_summary = f"""
SITE MODEL:
Base URL: {sm.get('base_url', 'unknown')}
Total Pages: {len(pages)}
Pages discovered:
{page_list}

API Endpoints ({len(api_endpoints)}):
{api_list}

Auth: {json.dumps(auth_info, indent=2) if auth_info else 'none detected'}
"""

        spec_text = context.spec_text or "No specific requirements provided."

        messages = [
            {"role": "system", "content": STRATEGY_SYSTEM_PROMPT},
            {"role": "user", "content": f"""
Create a test strategy for this application:

{site_summary}

REQUIREMENTS/SPEC:
{spec_text}

TARGET ENVIRONMENT: {context.environment}
"""}
        ]

        result = self._chat_json(messages, temperature=0.2)

        if "error" in result and "test_areas" not in result:
            return AgentResult(agent_name=self.name, status="error",
                               error=result.get("error", "Strategy generation failed"))

        return AgentResult(agent_name=self.name, status="ok", data=result)
