"""tests/unit/test_cognitive.py — Unit tests for cognitive agents."""
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from src.cognitive.agents.base_agent import AgentContext, AgentResult, BaseAgent
from src.llm.provider import LLMResponse


class DummyAgent(BaseAgent):
    name = "dummy"
    async def run(self, context: AgentContext) -> AgentResult:
        resp = self._chat([{"role": "user", "content": "test"}])
        return AgentResult(agent_name=self.name, status="ok", data={"reply": resp.content})


class TestBaseAgent:
    @pytest.mark.asyncio
    async def test_execute_success(self):
        mock_llm = MagicMock()
        mock_llm.chat.return_value = LLMResponse(
            content="ok", model="test", provider="test", usage={"total_tokens": 10})

        agent = DummyAgent(llm=mock_llm)
        ctx = AgentContext(tenant_id="t1")
        result = await agent.execute(ctx)

        assert result.status == "ok"
        assert result.llm_calls == 1
        assert result.tokens_used == 10
        assert result.duration_ms > 0

    @pytest.mark.asyncio
    async def test_execute_error(self):
        mock_llm = MagicMock()
        mock_llm.chat.side_effect = Exception("API down")

        agent = DummyAgent(llm=mock_llm)
        ctx = AgentContext(tenant_id="t1")
        result = await agent.execute(ctx)

        assert result.status == "error"
        assert "API down" in result.error


class TestAgentContext:
    def test_defaults(self):
        ctx = AgentContext(tenant_id="t1")
        assert ctx.environment == "SIT"
        assert ctx.site_model is None
        assert ctx.extra == {}

    def test_with_provider(self):
        ctx = AgentContext(tenant_id="t1", provider="anthropic", model="claude-sonnet-4-20250514")
        assert ctx.provider == "anthropic"


class TestTestStrategyAgent:
    @pytest.mark.asyncio
    async def test_strategy_generation(self):
        mock_llm = MagicMock()
        mock_llm.chat_json.return_value = {
            "strategy_name": "Login Tests",
            "test_areas": [{"area_id": "a1", "name": "Login", "pages": ["/login"],
                           "risk_level": "high", "depth": "deep",
                           "techniques": ["ui_functional"], "test_cases_estimate": 5,
                           "rationale": "Auth is critical"}],
            "execution_order": ["a1"],
            "estimated_total_cases": 5,
            "total_test_areas": 1,
        }

        from src.cognitive.agents.test_strategy import TestStrategyAgent
        agent = TestStrategyAgent(llm=mock_llm)
        ctx = AgentContext(
            tenant_id="t1",
            site_model={"base_url": "http://localhost", "pages": [{"url": "/login", "page_type": "login"}]},
            spec_text="Test login flow",
        )
        result = await agent.execute(ctx)

        assert result.status == "ok"
        assert len(result.data["test_areas"]) == 1


class TestTestGeneratorAgent:
    @pytest.mark.asyncio
    async def test_generation(self):
        mock_llm = MagicMock()
        mock_llm.chat_json.return_value = {
            "area_id": "a1",
            "test_file_name": "test_login.py",
            "test_count": 3,
            "code": "import pytest\n\ndef test_login(): pass",
            "fixtures_needed": ["page"],
            "dependencies": ["pytest"],
        }

        from src.cognitive.agents.test_generator import TestGeneratorAgent
        agent = TestGeneratorAgent(llm=mock_llm)
        ctx = AgentContext(
            tenant_id="t1",
            target_url="http://localhost:8000",
            extra={"test_area": {"area_id": "a1", "name": "Login", "pages": ["/login"]}},
        )
        result = await agent.execute(ctx)

        assert result.status == "ok"
        assert "test_login.py" in result.data["test_file_name"]


class TestFailureTriageAgent:
    @pytest.mark.asyncio
    async def test_triage(self):
        mock_llm = MagicMock()
        mock_llm.chat_json.return_value = {
            "triaged_failures": [
                {"test_name": "test_x", "category": "BUG", "confidence": 0.9,
                 "reasoning": "Real bug", "suggested_action": "File bug", "severity": "high"}
            ],
            "summary": {"total": 1, "bugs": 1, "flaky": 0, "environment": 0, "data": 0, "stale_selector": 0},
        }

        from src.cognitive.agents.failure_triage import FailureTriageAgent
        agent = FailureTriageAgent(llm=mock_llm)
        ctx = AgentContext(
            tenant_id="t1",
            extra={"failures": [{"test_name": "test_x", "error_message": "AssertionError"}]},
        )
        result = await agent.execute(ctx)

        assert result.status == "ok"
        assert result.data["summary"]["bugs"] == 1


class TestPipelineResult:
    def test_to_dict(self):
        from src.cognitive.orchestrator import PipelineResult
        pr = PipelineResult(status="ok", total_llm_calls=5, total_tokens=1000)
        d = pr.to_dict()
        assert d["status"] == "ok"
        assert d["total_llm_calls"] == 5
