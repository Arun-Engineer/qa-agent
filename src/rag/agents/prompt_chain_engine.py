"""agents/prompt_chain_engine.py — Prompt Chain Execution Engine for UI/E2E Testing

Chain: UNDERSTAND → DISCOVER → PLAN → AUTH(conditional) → GENERATE → REVIEW → EXECUTE → ANALYZE → REPORT
Each step uses a versioned prompt template. Outputs pipe into subsequent step inputs.
"""
from __future__ import annotations
import time, uuid, structlog, asyncio
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional
logger = structlog.get_logger()

class StepStatus(str, Enum):
    PENDING="pending"; RUNNING="running"; COMPLETED="completed"; FAILED="failed"; SKIPPED="skipped"

@dataclass
class ChainStep:
    name: str; prompt_template_name: str; description: str = ""
    input_mapping: dict[str, str] = field(default_factory=dict)
    condition: Optional[Callable[[dict], bool]] = None
    max_retries: int = 1; retry_delay_seconds: float = 2.0; output_key: str = ""
    validate_fn: Optional[Callable[[Any], bool]] = None
    step_type: str = "llm"; timeout_seconds: float = 120.0

@dataclass
class StepResult:
    step_name: str; status: StepStatus; output: Any = None; error: str = ""
    duration_ms: float = 0.0; llm_calls: int = 0; tokens_used: int = 0; retries: int = 0

@dataclass
class ChainResult:
    chain_id: str; chain_name: str; status: str; steps: list[StepResult] = field(default_factory=list)
    final_output: Any = None; total_duration_ms: float = 0.0
    total_llm_calls: int = 0; total_tokens: int = 0; context: dict[str, Any] = field(default_factory=dict)

def ui_test_chain() -> list[ChainStep]:
    return [
        ChainStep(name="understand_spec", prompt_template_name="spec_understanding", output_key="parsed_spec", step_type="llm"),
        ChainStep(name="discover_site", prompt_template_name="site_discovery", input_mapping={"target_url":"parsed_spec.target_url"}, output_key="site_model", step_type="tool"),
        ChainStep(name="plan_tests", prompt_template_name="test_plan_generation",
            input_mapping={"spec_text":"parsed_spec.structured_spec","test_type":"parsed_spec.test_type","target_url":"parsed_spec.target_url","context":"site_model.summary"},
            output_key="test_plan", step_type="llm"),
        ChainStep(name="generate_auth", prompt_template_name="auth_code_generation",
            input_mapping={"target_url":"parsed_spec.target_url","credentials":"parsed_spec.credentials","site_model":"site_model.login_page"},
            condition=lambda ctx: ctx.get("site_model",{}).get("login_wall_detected",False) or ctx.get("parsed_spec",{}).get("requires_auth",False),
            output_key="auth_code", step_type="llm"),
        ChainStep(name="generate_tests", prompt_template_name="ui_test_generation",
            input_mapping={"spec_text":"test_plan.scenarios_json","target_url":"parsed_spec.target_url","login_required":"auth_code.has_auth","credentials":"parsed_spec.credentials"},
            output_key="test_code", step_type="llm", max_retries=2),
        ChainStep(name="self_review_code", prompt_template_name="code_self_review",
            input_mapping={"test_code":"test_code.code","test_plan":"test_plan.scenarios_json"},
            output_key="reviewed_code", step_type="llm"),
        ChainStep(name="execute_tests", prompt_template_name="test_execution",
            input_mapping={"test_code":"reviewed_code.final_code","auth_code":"auth_code.code"},
            output_key="test_results", step_type="code_exec", timeout_seconds=300.0),
        ChainStep(name="analyze_results", prompt_template_name="bug_analysis",
            input_mapping={"test_results":"test_results.raw_results","test_plan":"test_plan.scenarios_json"},
            output_key="analysis", step_type="llm"),
        ChainStep(name="generate_report", prompt_template_name="report_generation",
            input_mapping={"test_results":"test_results.raw_results","analysis":"analysis.summary","test_plan":"test_plan.plan_name"},
            output_key="report", step_type="tool"),
    ]

def api_test_chain() -> list[ChainStep]:
    return [
        ChainStep(name="understand_spec", prompt_template_name="spec_understanding", output_key="parsed_spec", step_type="llm"),
        ChainStep(name="plan_api_tests", prompt_template_name="test_plan_generation", input_mapping={"spec_text":"parsed_spec.structured_spec","test_type":"api"}, output_key="test_plan", step_type="llm"),
        ChainStep(name="generate_api_tests", prompt_template_name="api_test_generation", input_mapping={"api_spec":"test_plan.scenarios_json","base_url":"parsed_spec.target_url"}, output_key="test_code", step_type="llm", max_retries=2),
        ChainStep(name="execute_tests", prompt_template_name="test_execution", input_mapping={"test_code":"test_code.code"}, output_key="test_results", step_type="code_exec", timeout_seconds=180.0),
        ChainStep(name="analyze_results", prompt_template_name="bug_analysis", input_mapping={"test_results":"test_results.raw_results"}, output_key="analysis", step_type="llm"),
    ]

def spec_review_chain() -> list[ChainStep]:
    return [
        ChainStep(name="analyze_spec", prompt_template_name="spec_review_5d", output_key="review", step_type="llm"),
        ChainStep(name="suggest_tests", prompt_template_name="test_plan_generation", input_mapping={"spec_text":"review.raw_spec","test_type":"suggested","context":"review.findings"}, output_key="suggested_tests", step_type="llm"),
    ]

CHAIN_REGISTRY: dict[str, Callable[[], list[ChainStep]]] = {"ui_test": ui_test_chain, "api_test": api_test_chain, "spec_review": spec_review_chain}

class PromptChainEngine:
    def __init__(self, prompt_registry=None, llm_provider=None, tracer=None, cost_tracker=None, security_guards=None):
        self._registry = prompt_registry; self._llm = llm_provider; self._tracer = tracer
        self._cost_tracker = cost_tracker; self._security = security_guards or {}

    @property
    def llm(self):
        if self._llm is None:
            from src.llm.provider import get_llm; self._llm = get_llm()
        return self._llm

    @property
    def registry(self):
        if self._registry is None:
            from src.rag.prompts.registry import PromptRegistry; self._registry = PromptRegistry()
        return self._registry

    async def execute(self, chain_name: str, initial_context: dict[str, Any], tenant_id: str = "") -> ChainResult:
        if chain_name not in CHAIN_REGISTRY: raise ValueError(f"Unknown chain: {chain_name}")
        steps = CHAIN_REGISTRY[chain_name](); chain_id = f"chain_{uuid.uuid4().hex[:10]}"
        start = time.time(); context = dict(initial_context); step_results: list[StepResult] = []
        trace_id = self._tracer.start_trace(chain_name, tenant_id=tenant_id) if self._tracer else None

        for step in steps:
            if step.condition and not step.condition(context):
                step_results.append(StepResult(step_name=step.name, status=StepStatus.SKIPPED)); continue
            result = await self._exec_step(step, context, trace_id)
            step_results.append(result)
            if result.status == StepStatus.COMPLETED and result.output is not None:
                context[step.output_key or step.name] = result.output
            if result.status == StepStatus.FAILED: break

        if self._tracer and trace_id: self._tracer.end_trace(trace_id)
        elapsed = (time.time() - start) * 1000
        failed = sum(1 for r in step_results if r.status == StepStatus.FAILED)
        completed = sum(1 for r in step_results if r.status == StepStatus.COMPLETED)
        status = "completed" if failed == 0 else ("partial" if completed > 0 else "failed")
        return ChainResult(chain_id=chain_id, chain_name=chain_name, status=status, steps=step_results,
            final_output=context.get(steps[-1].output_key) if steps else None,
            total_duration_ms=round(elapsed,1), total_llm_calls=sum(r.llm_calls for r in step_results),
            total_tokens=sum(r.tokens_used for r in step_results), context=context)

    async def _exec_step(self, step: ChainStep, context: dict, trace_id: str|None) -> StepResult:
        last_error = ""
        for attempt in range(step.max_retries + 1):
            start = time.time()
            span_ctx = self._tracer.span(trace_id, step.name) if self._tracer and trace_id else None
            span = span_ctx.__enter__() if span_ctx else None
            try:
                vars = self._resolve(step.input_mapping, context)
                if not step.input_mapping and "spec_text" in context: vars["spec_text"] = context["spec_text"]
                if "target_url" in context and "target_url" not in vars: vars["target_url"] = context.get("target_url","")

                try:
                    template = self.registry.get(step.prompt_template_name)
                    messages = template.to_messages(**vars)
                    temperature = template.temperature
                except (KeyError, ValueError):
                    messages = [{"role":"system","content":f"QA expert. Task: {step.description}"},{"role":"user","content":str(vars)}]
                    temperature = 0.2

                output: Any; llm_calls = 0; tokens = 0

                if step.step_type == "llm":
                    input_guard = self._security.get("input")
                    if input_guard:
                        last_msg = messages[-1]["content"] if messages else ""
                        guard_res = input_guard.check(last_msg)
                        if not guard_res.is_safe:
                            raise RuntimeError(f"input_guard_blocked: {guard_res.threats_detected}")
                        if guard_res.sanitized_input != last_msg:
                            messages[-1]["content"] = guard_res.sanitized_input

                    resp = await asyncio.wait_for(
                        asyncio.to_thread(self.llm.chat_json, messages, temperature=temperature),
                        timeout=step.timeout_seconds,
                    )
                    output = resp if isinstance(resp, dict) else {"raw": resp}
                    llm_calls = 1
                    tokens = int(output.get("_usage", {}).get("total_tokens", 0)) if isinstance(output, dict) else 0

                    output_filter = self._security.get("output")
                    if output_filter:
                        summary = str(output)[:4000]
                        of_res = output_filter.check(summary)
                        if not of_res.is_safe:
                            output["_output_issues"] = of_res.issues

                    if step.validate_fn and not step.validate_fn(output):
                        raise ValueError(f"validate_fn rejected output for {step.name}")

                elif step.step_type == "tool":
                    tool_fn = self._security.get("tools", {}).get(step.name) if isinstance(self._security.get("tools"), dict) else None
                    if callable(tool_fn):
                        output = await asyncio.wait_for(asyncio.to_thread(tool_fn, vars), timeout=step.timeout_seconds)
                    else:
                        output = {"step": step.name, "vars": vars, "status": "tool_not_registered"}

                elif step.step_type == "code_exec":
                    executor = self._security.get("executor")
                    if callable(executor):
                        output = await asyncio.wait_for(asyncio.to_thread(executor, vars), timeout=step.timeout_seconds)
                    else:
                        output = {"step": step.name, "vars": vars, "status": "executor_not_registered", "raw_results": {}}
                else:
                    output = {"step": step.name, "vars": vars, "status": "executed"}

                if span:
                    span.llm_calls = llm_calls
                    span.tokens_used = tokens

                elapsed = (time.time() - start) * 1000
                if span_ctx: span_ctx.__exit__(None, None, None)
                return StepResult(step_name=step.name, status=StepStatus.COMPLETED, output=output,
                                  duration_ms=round(elapsed, 1), llm_calls=llm_calls, tokens_used=tokens, retries=attempt)
            except Exception as e:
                last_error = str(e)
                if span_ctx: span_ctx.__exit__(type(e), e, None)
                if attempt < step.max_retries: await asyncio.sleep(step.retry_delay_seconds * (attempt+1))
        return StepResult(step_name=step.name, status=StepStatus.FAILED, error=last_error, retries=step.max_retries)

    def _resolve(self, mapping: dict[str, str], context: dict) -> dict[str, Any]:
        resolved = {}
        for var, path in mapping.items():
            parts = path.split("."); cur = context
            for p in parts:
                cur = cur.get(p) if isinstance(cur, dict) else None
                if cur is None: break
            resolved[var] = cur if cur is not None else ""
        return resolved
