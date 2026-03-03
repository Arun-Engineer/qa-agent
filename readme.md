# QA Agent v5 — Phase 0 + 1 (Consolidated) + Phase 2 (Discovery Engine)

## What's in this delivery

### Phase 0: Housekeeping
- `scripts/phase0_housekeeping.sh` — Run once to clean .env, __pycache__, .idea from git
- `.pre-commit-config.yaml` — Ruff + secret detection + pre-commit hooks
- `.env.example` — Safe template (replaces the committed .env)
- `.gitignore` additions via the housekeeping script
- `pyproject.toml` — Modern Python project config

### Phase 1: Consolidated Foundation (deduplicated)
All existing Phase 1 files unified under `src/`:

| File | Role |
|------|------|
| `src/session/session_context.py` | SessionContext dataclass (Environment, AccessMode, ENV_RULES) |
| `src/session/session_store.py` | In-memory session + run storage |
| `src/session/env_registry.py` | YAML-based environment config loader |
| `src/api/dependencies.py` | Singleton stores (get_store, get_env_registry) |
| `src/models/schemas.py` | Pydantic request/response models |
| `src/api/middleware/audit.py` | Request logging with structlog |
| `src/api/middleware/auth.py` | API key authentication |
| `src/api/middleware/rate_limit.py` | Per-IP rate limiting |
| `src/api/routes/sessions.py` | Session CRUD API |
| `src/api/routes/runs.py` | Test run lifecycle API |
| `src/api/routes/environments.py` | Environment config API |
| `src/api/main.py` | FastAPI factory (standalone + importable) |
| `src/guardrails/prod_safety.py` | PROD read-only action enforcer |
| `config/environments.yaml` | SIT/UAT/PROD definitions |

### Phase 2: Discovery Engine (NEW)
Zero-knowledge site discovery — 6 production modules + orchestrator:

| File | Role |
|------|------|
| `src/discovery/site_model.py` | Central data structure: pages + components + APIs → JSON/Neo4j |
| `src/discovery/auth_handler.py` | Login detection (form/OAuth), credential autofill |
| `src/discovery/site_crawler.py` | Playwright BFS/DFS crawler with SPA + cycle detection |
| `src/discovery/page_classifier.py` | Heuristic + LLM page type classification (25+ patterns) |
| `src/discovery/component_fingerprinter.py` | DOM traversal → UI component taxonomy |
| `src/discovery/api_surface_mapper.py` | Network interception → API endpoint catalog |
| `src/discovery/engine.py` | Orchestrator: ties all 6 modules into a single pipeline |
| `src/api/routes/discovery.py` | REST API: POST /api/v1/discovery/, GET status/pages/api-surface |
| `config/discovery.yaml` | Crawl, screenshot, classification, fingerprinting config |

## How to integrate

### Step 1: Run Phase 0 housekeeping
```bash
chmod +x scripts/phase0_housekeeping.sh
./scripts/phase0_housekeeping.sh
git add -A && git commit -m "chore: Phase 0 housekeeping"
```

### Step 2: Copy consolidated src/ into your repo
```bash
cp -r src/ /path/to/qa-agent/src/
cp -r config/ /path/to/qa-agent/config/
cp -r tests/ /path/to/qa-agent/tests/
cp pyproject.toml requirements-phase2.txt requirements-dev.txt /path/to/qa-agent/
```

### Step 3: Patch asgi.py (1 line)
Add this to your `asgi.py` after the existing Phase 1 route includes:
```python
from src.api.routes.discovery import router as discovery_router
tenant_app.include_router(discovery_router, prefix="/api/v1/discovery", tags=["Discovery"])
```

### Step 4: Install dependencies
```bash
pip install -r requirements-phase2.txt
playwright install chromium
```

### Step 5: Run tests
```bash
pytest tests/unit/ -v         # All unit tests (no browser needed)
pytest tests/integration/ -v  # API integration tests (mocked browser)
```

## Architecture alignment

This delivery plugs directly into your existing architecture:
- **Entry point**: `asgi.py` (Starlette host routing) — unchanged
- **Auth**: `auth/` (User, bcrypt, session) — unchanged
- **Tenancy**: `tenancy/` (Tenant, Account, Membership, RBAC) — unchanged
- **Agent**: `agent/` (planner, runner, verifier) — unchanged
- **New**: `src/discovery/` — Phase 2 discovery engine
- **Consolidated**: `src/session/`, `src/api/`, `src/models/` — deduplicated Phase 1

The discovery engine output (SiteModel) is designed to be consumed by Phase 3 cognitive agents.
