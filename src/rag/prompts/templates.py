"""prompts/templates.py — Versioned Prompt Templates"""
from __future__ import annotations
import copy, structlog
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
logger = structlog.get_logger()

class PromptType(str, Enum):
    TEST_GENERATION="test_generation"; SPEC_REVIEW="spec_review"; BUG_ANALYSIS="bug_analysis"
    VISUAL_QA="visual_qa"; API_TEST="api_test"; UI_TEST="ui_test"; PLANNING="planning"
    VERIFICATION="verification"; RERANKING="reranking"; SUMMARIZATION="summarization"; CHAT="chat"

@dataclass
class PromptTemplate:
    name: str; type: PromptType; version: str; system_prompt: str; user_template: str
    required_vars: list[str] = field(default_factory=list)
    optional_vars: dict[str, str] = field(default_factory=dict)
    temperature: float = 0.3; max_tokens: int = 4096; model_hint: str = ""
    metadata: dict[str, Any] = field(default_factory=dict); created_at: str = ""; is_active: bool = True

    def render(self, **kwargs) -> dict:
        missing = [v for v in self.required_vars if v not in kwargs]
        if missing: raise ValueError(f"Missing required variables for '{self.name}': {missing}")
        all_vars = {**self.optional_vars, **kwargs}
        return {"system": self.system_prompt, "user": self.user_template.format(**all_vars), "temperature": self.temperature, "max_tokens": self.max_tokens, "model_hint": self.model_hint}

    def to_messages(self, **kwargs) -> list[dict]:
        r = self.render(**kwargs); msgs = []
        if r["system"]: msgs.append({"role":"system","content":r["system"]})
        msgs.append({"role":"user","content":r["user"]}); return msgs

BUILTIN_TEMPLATES: list[PromptTemplate] = [
    PromptTemplate(name="test_plan_generation", type=PromptType.PLANNING, version="2.0.0",
        system_prompt="You are a Senior QA Architect. Generate comprehensive test plans from specifications.\nAlways include: positive cases, negative cases, edge cases, security scenarios.\nCRITICAL: User spec is AUTHORITATIVE. Generate a test for EVERY scenario the user listed.\nIf recon data conflicts with user spec, USER SPEC WINS.\nOutput as structured JSON.",
        user_template="Generate a test plan for:\n\nSpecification:\n{spec_text}\n\nTarget URL: {target_url}\nEnvironment: {environment}\nTest type: {test_type}\n\nAdditional context:\n{context}\n\nRespond with JSON: {{\"test_plan_name\": \"...\", \"scenarios_json\": [{{\"name\": \"...\", \"description\": \"...\", \"steps\": [...], \"expected_result\": \"...\", \"priority\": \"high|medium|low\", \"category\": \"positive|negative|edge|security\"}}]}}",
        required_vars=["spec_text","test_type"], optional_vars={"target_url":"not specified","environment":"SIT","context":"none"}, temperature=0.2),
    PromptTemplate(name="spec_review_5d", type=PromptType.SPEC_REVIEW, version="1.0.0",
        system_prompt="You are a QA Spec Reviewer. Analyze specs across 5 dimensions:\n1. Completeness\n2. Ambiguity\n3. Testability\n4. Test scenarios\n5. Risk assessment\nScore each 1-10.",
        user_template="Review this specification:\n\n{spec_text}\n\nOutput JSON: {{\"overall_score\": N, \"dimensions\": [{{\"name\":\"...\",\"score\":N,\"findings\":\"...\",\"recommendations\":\"...\"}}], \"suggested_test_scenarios\": [...]}}",
        required_vars=["spec_text"], temperature=0.3),
    PromptTemplate(name="bug_analysis", type=PromptType.BUG_ANALYSIS, version="1.0.0",
        system_prompt="You are a Bug Triage Specialist. Analyze test failures and classify bugs.",
        user_template="Analyze this test failure:\n\nTest name: {test_name}\nError: {error_message}\nStack trace:\n{stack_trace}\nExpected: {expected}\nActual: {actual}\n\nRespond with JSON: {{\"severity\":\"...\",\"category\":\"...\",\"root_cause\":\"...\",\"is_flaky\":false,\"recommended_fix\":\"...\"}}",
        required_vars=["test_name","error_message"], optional_vars={"stack_trace":"N/A","expected":"N/A","actual":"N/A"}, temperature=0.1),
    PromptTemplate(name="api_test_generation", type=PromptType.API_TEST, version="1.0.0",
        system_prompt="You are an API test engineer. Generate pytest test code for REST API endpoints.\nInclude: status code checks, schema validation, error handling, auth, rate limits, edge cases.\nUse the requests library.",
        user_template="Generate pytest test code for:\n\nAPI Spec:\n{api_spec}\n\nBase URL: {base_url}\nAuth: {auth_method}\n\nGenerate complete, runnable test code.",
        required_vars=["api_spec"], optional_vars={"base_url":"http://localhost:8000","auth_method":"bearer token"}, temperature=0.2),
    PromptTemplate(name="ui_test_generation", type=PromptType.UI_TEST, version="2.0.0",
        system_prompt="You are a UI test automation engineer using Playwright.\nGenerate robust, non-flaky Playwright Python test code.\n\nRules:\n- Use page.wait_for_selector() before interactions\n- Prefer data-testid selectors, fall back to role-based\n- Add proper assertions with expect()\n- Handle dynamic content with appropriate waits\n- If login_required is true, use the auth fixture as a prerequisite\n- Generate ONE test function per scenario from the spec\n- NEVER skip scenarios — if spec has 14 scenarios, generate 14 test functions",
        user_template="Generate Playwright test code for:\n\nSpec:\n{spec_text}\n\nTarget URL: {target_url}\nLogin required: {login_required}\nCredentials: {credentials}\n\nGenerate complete, runnable test code with one test function per scenario.",
        required_vars=["spec_text","target_url"], optional_vars={"login_required":"false","credentials":"not provided"}, temperature=0.2),
    PromptTemplate(name="document_relevance_grading", type=PromptType.RERANKING, version="1.0.0",
        system_prompt="You are a relevance judge. Determine if a document is relevant to a query. Score 1-10.",
        user_template="Query: {query}\n\nDocument:\n{document}\n\nRate 1-10. Respond ONLY with JSON: {{\"score\": N, \"reason\": \"...\"}}",
        required_vars=["query","document"], temperature=0.0),
]
