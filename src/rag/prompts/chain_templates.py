"""prompts/chain_templates.py — Prompt Templates for Chain Execution"""
from src.rag.prompts.templates import PromptTemplate, PromptType

CHAIN_TEMPLATES: list[PromptTemplate] = [
    PromptTemplate(name="spec_understanding", type=PromptType.PLANNING, version="1.0.0",
        system_prompt="You are a QA Spec Parser. Extract structured information.\nExtract EVERY scenario the user mentions. Never merge or skip scenarios.\nIf credentials are provided, set requires_auth=true.",
        user_template="Parse this spec:\n\n{spec_text}\n\nRespond with JSON: {{\"test_type\":\"ui|api|e2e\",\"target_url\":\"...\",\"scenarios\":[...],\"credentials\":{{...}},\"requires_auth\":true/false,\"structured_spec\":\"...\"}}",
        required_vars=["spec_text"], temperature=0.1),
    PromptTemplate(name="site_discovery", type=PromptType.PLANNING, version="1.0.0",
        system_prompt="You are a site analysis expert.",
        user_template="Analyze target: {target_url}\nRespond JSON: {{\"login_wall_likely\":bool,\"predicted_pages\":[...],\"navigation_type\":\"spa|mpa|hybrid\"}}",
        required_vars=["target_url"], temperature=0.2),
    PromptTemplate(name="auth_code_generation", type=PromptType.UI_TEST, version="1.0.0",
        system_prompt="You are a Playwright auth code specialist.\nGenerate a reusable login fixture.\nUse wait_for_selector before every interaction.\nPrefer data-testid > role > CSS selectors.\nMake it a pytest fixture reusable by all test functions.",
        user_template="Generate Playwright login code:\nURL: {target_url}\nCredentials: {credentials}\nSite model: {site_model}\n\nRespond JSON: {{\"has_auth\":true,\"code\":\"...fixture code...\",\"selector_strategy\":\"...\"}}",
        required_vars=["target_url"], optional_vars={"credentials":"{}","site_model":"no model"}, temperature=0.1),
    PromptTemplate(name="code_self_review", type=PromptType.VERIFICATION, version="1.0.0",
        system_prompt="You are a Playwright test code reviewer.\nCheck: missing waits, wrong selectors, missing assertions, hardcoded values, flaky patterns, import errors, syntax errors, coverage gaps.\nFix ALL issues. Return corrected code.",
        user_template="Review and fix:\n```python\n{test_code}\n```\nTest plan:\n{test_plan}\n\nRespond JSON: {{\"issues_found\":[...],\"fixes_applied\":[...],\"final_code\":\"...\",\"coverage_check\":{{\"planned\":N,\"covered\":N}}}}",
        required_vars=["test_code","test_plan"], temperature=0.1),
    PromptTemplate(name="test_execution", type=PromptType.VERIFICATION, version="1.0.0",
        system_prompt="Prepare test code for execution with all imports, fixtures, conftest.",
        user_template="Test code:\n{test_code}\nAuth code:\n{auth_code}\n\nRespond JSON: {{\"runnable_code\":\"...\",\"conftest_code\":\"...\",\"requirements\":[...]}}",
        required_vars=["test_code"], optional_vars={"auth_code":"# no auth"}, temperature=0.1),
    PromptTemplate(name="report_generation", type=PromptType.SUMMARIZATION, version="1.0.0",
        system_prompt="Generate a QA report executive summary for stakeholders.",
        user_template="Results: {test_results}\nAnalysis: {analysis}\nPlan: {test_plan}\n\nRespond JSON: {{\"executive_summary\":\"...\",\"total_tests\":N,\"passed\":N,\"failed\":N,\"critical_failures\":[...],\"recommendations\":[...]}}",
        required_vars=["test_results"], optional_vars={"analysis":"none","test_plan":"QA Report"}, temperature=0.3),
]
