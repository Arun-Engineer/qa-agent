"""
src/cognitive/orchestrator.py — Cognitive Agent Orchestrator.

Coordinates the full pipeline:
  1. Test Strategy (from site model + spec)
  2. Test Generation (for each area)
  3. Test Execution (Playwright/Pytest)
  4. Failure Triage (classify failures)
  5. Self-Healing (fix broken tests)

Can run full pipeline or individual agents.
"""
from __future__ import annotations

import json, time, structlog
from dataclasses import dataclass, field
from typing import Optional
from pathlib import Path

from src.cognitive.agents.base_agent import AgentContext, AgentResult
from src.cognitive.agents.test_strategy import TestStrategyAgent
from src.cognitive.agents.test_generator import TestGeneratorAgent
from src.cognitive.agents.failure_triage import FailureTriageAgent
from src.cognitive.agents.self_healer import SelfHealerAgent
from src.llm.provider import get_llm

logger = structlog.get_logger()


@dataclass
class PipelineResult:
    status: str = "ok"
    strategy: Optional[dict] = None
    generated_tests: list = field(default_factory=list)
    execution_results: Optional[dict] = None
    triage: Optional[dict] = None
    healed_tests: list = field(default_factory=list)
    total_duration_ms: float = 0
    total_llm_calls: int = 0
    total_tokens: int = 0
    errors: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "strategy": self.strategy,
            "generated_tests": self.generated_tests,
            "execution_results": self.execution_results,
            "triage": self.triage,
            "healed_tests": self.healed_tests,
            "total_duration_ms": self.total_duration_ms,
            "total_llm_calls": self.total_llm_calls,
            "total_tokens": self.total_tokens,
            "errors": self.errors,
        }


class CognitiveOrchestrator:
    """Coordinates cognitive agents in a pipeline."""

    def __init__(self, provider: str | None = None, model: str | None = None):
        llm = get_llm(provider=provider, model=model) if (provider or model) else None
        self.strategy_agent = TestStrategyAgent(llm)
        self.generator_agent = TestGeneratorAgent(llm)
        self.triage_agent = FailureTriageAgent(llm)
        self.healer_agent = SelfHealerAgent(llm)

    async def run_strategy(self, context: AgentContext) -> AgentResult:
        return await self.strategy_agent.execute(context)

    async def run_generation(self, context: AgentContext, strategy: dict) -> list[AgentResult]:
        return await self.generator_agent.generate_for_strategy(context, strategy)

    async def run_triage(self, context: AgentContext, failures: list[dict]) -> AgentResult:
        context.extra["failures"] = failures
        return await self.triage_agent.execute(context)

    async def run_healing(self, context: AgentContext, failed_test: dict,
                          dom_snapshot: str = "") -> AgentResult:
        context.extra["failed_test"] = failed_test
        context.extra["dom_snapshot"] = dom_snapshot
        return await self.healer_agent.execute(context)

    async def run_full_pipeline(
        self,
        context: AgentContext,
        execute_tests: bool = False,
        auto_heal: bool = False,
    ) -> PipelineResult:
        """
        Full pipeline: Strategy → Generate → (Execute) → (Triage) → (Heal)
        """
        result = PipelineResult()
        start = time.time()

        # 1. Strategy
        logger.info("pipeline_step", step="strategy", tenant=context.tenant_id)
        strategy_result = await self.run_strategy(context)
        result.total_llm_calls += strategy_result.llm_calls
        result.total_tokens += strategy_result.tokens_used

        if strategy_result.status != "ok":
            result.status = "error"
            result.errors.append(f"Strategy failed: {strategy_result.error}")
            result.total_duration_ms = round((time.time() - start) * 1000, 2)
            return result

        result.strategy = strategy_result.data

        # 2. Generate tests for each area
        logger.info("pipeline_step", step="generation",
                     areas=len(result.strategy.get("test_areas", [])))
        gen_results = await self.run_generation(context, result.strategy)

        for gr in gen_results:
            result.total_llm_calls += gr.llm_calls
            result.total_tokens += gr.tokens_used
            if gr.status == "ok" and gr.data:
                result.generated_tests.append(gr.data)
            elif gr.error:
                result.errors.append(f"Generation error: {gr.error}")

        # 3. Save generated tests
        output_dir = Path("data/generated_tests") / context.tenant_id
        output_dir.mkdir(parents=True, exist_ok=True)

        for test_data in result.generated_tests:
            fname = test_data.get("test_file_name", "test_unknown.py")
            code = test_data.get("code", "")
            if code:
                (output_dir / fname).write_text(code, encoding="utf-8")
                logger.info("test_saved", file=str(output_dir / fname))

        # 4. Execute tests (optional)
        if execute_tests and result.generated_tests:
            logger.info("pipeline_step", step="execution")
            try:
                import subprocess
                exec_result = subprocess.run(
                    ["python", "-m", "pytest", str(output_dir), "-v",
                     "--tb=short", "--json-report", "--json-report-file=-"],
                    capture_output=True, text=True, timeout=300,
                    cwd=str(Path.cwd()),
                )
                try:
                    result.execution_results = json.loads(exec_result.stdout)
                except json.JSONDecodeError:
                    result.execution_results = {
                        "stdout": exec_result.stdout[-2000:],
                        "stderr": exec_result.stderr[-1000:],
                        "returncode": exec_result.returncode,
                    }
            except Exception as e:
                result.errors.append(f"Execution error: {str(e)}")

            # 5. Triage failures
            failures = self._extract_failures(result.execution_results)
            if failures:
                logger.info("pipeline_step", step="triage", failures=len(failures))
                triage_result = await self.run_triage(context, failures)
                result.total_llm_calls += triage_result.llm_calls
                result.total_tokens += triage_result.tokens_used
                result.triage = triage_result.data

                # 6. Self-heal (optional)
                if auto_heal and triage_result.data:
                    stale = [
                        t for t in triage_result.data.get("triaged_failures", [])
                        if t.get("category") == "STALE_SELECTOR"
                    ]
                    for s in stale[:5]:
                        heal_ctx = AgentContext(
                            tenant_id=context.tenant_id,
                            target_url=context.target_url,
                            environment=context.environment,
                            provider=context.provider,
                            model=context.model,
                            extra={"failed_test": s},
                        )
                        heal_result = await self.healer_agent.execute(heal_ctx)
                        result.total_llm_calls += heal_result.llm_calls
                        result.total_tokens += heal_result.tokens_used
                        if heal_result.data:
                            result.healed_tests.append(heal_result.data)

        result.total_duration_ms = round((time.time() - start) * 1000, 2)
        logger.info("pipeline_complete",
                     duration_ms=result.total_duration_ms,
                     llm_calls=result.total_llm_calls,
                     tokens=result.total_tokens,
                     tests_generated=len(result.generated_tests),
                     errors=len(result.errors))
        return result

    @staticmethod
    def _extract_failures(exec_results: dict | None) -> list[dict]:
        if not exec_results:
            return []
        tests = exec_results.get("tests", [])
        return [
            {
                "test_name": t.get("nodeid", "unknown"),
                "error_message": t.get("call", {}).get("crash", {}).get("message", ""),
                "traceback": t.get("call", {}).get("longrepr", ""),
                "duration_ms": t.get("duration", 0) * 1000,
            }
            for t in tests if t.get("outcome") == "failed"
        ]
