# MVP Orchestration Engine — Installation Guide

## What's Included

### Core Engine (`agent/core/`)
| File | Purpose |
|------|---------|
| `orchestrator.py` | State machine: INIT → PLANNING → EXECUTING → VERIFYING → REPORTING → DONE |
| `errors.py` | Structured error hierarchy (PlanningError, ToolError, TimeoutError, etc.) |
| `base_workflow.py` | Abstract base class all workflows implement |
| `llm_client.py` | LLM wrapper with retry, backoff, token tracking |
| `__init__.py` | Package init |

### Workflows (`agent/workflows/`)
| File | Purpose |
|------|---------|
| `api_test.py` | API Test Agent — spec → pytest generation → execution → report |
| `ui_test.py` | UI Test Agent — spec → site recon → Playwright tests → report |
| `spec_review.py` | Spec Review Agent — analyzes gaps, ambiguity, testability (no execution) |
| `__init__.py` | Workflow registry (maps names to implementations) |

### Updated Runner
| File | Purpose |
|------|---------|
| `agent_runner.py` | Updated to use orchestrator. **100% backward compatible** with existing API. |

## Installation

```bash
# From your qa_agent root:

# 1. Copy core engine
cp -r agent/core/ <from_package>/agent/core/

# 2. Copy workflows
cp -r agent/workflows/ <from_package>/agent/workflows/

# 3. BACKUP then replace agent_runner.py
cp agent/agent_runner.py agent/agent_runner.py.bak
cp <from_package>/agent/agent_runner.py agent/agent_runner.py
```

## What Changes

### Before (old agent_runner.py)
- Flat script, no retry, no timeout
- Hangs forever if LLM doesn't respond
- One workflow: plan → execute → report
- No error classification

### After (new orchestration engine)
- State machine with clear phases
- 3 retries with exponential backoff (1s, 2s, 4s)
- 120s timeout per step, 60s for planning
- 3 distinct workflows: API test, UI test, Spec review
- Every error classified (timeout, auth_error, rate_limited, etc.)
- Event hooks for console/logging
- Context passing between steps

## API Compatibility

The existing API endpoints work WITHOUT changes:

```python
# Old way — still works:
from agent.agent_runner import run_agent_from_spec
result = run_agent_from_spec(spec)

# New way — select workflow:
result = run_agent_from_spec(
    spec,
    workflow_name="api_test",  # or "ui_test" or "spec_review"
    context={"tenant_id": "...", "account_id": "..."},
)
```

## Environment Variables (optional)

| Variable | Default | Purpose |
|----------|---------|---------|
| `QA_MAX_RETRIES` | 3 | Max retry attempts per step |
| `QA_STEP_TIMEOUT` | 120 | Seconds before step times out |
| `QA_PLAN_TIMEOUT` | 60 | Seconds before planning times out |
| `QA_DISABLE_RECON` | 0 | Set to 1 to skip site recon |
| `FORCE_REGEN_TESTS` | 0 | Set to 1 to always regenerate test files |
