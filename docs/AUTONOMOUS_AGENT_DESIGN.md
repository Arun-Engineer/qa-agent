# Autonomous QA Agent — 6-Phase Design

**Target flow:** User pastes a URL. Agent explores the app, builds a model, proposes
workflows, pauses to request credentials when auth is needed, runs tests at UI + API
+ behavioral layers with bounded self-healing, classifies findings, and stores the
run so future runs are smarter.

This document is the source of truth for the build-out. Each phase is shippable on
its own and unblocks the next.

---

## Phase 1 — Foundation (URL-only run mode + run state machine)

**Goal:** Accept a URL as the only input. Drive the run via a resumable state
machine that can pause and ask the user for credentials mid-run.

**Deliverables:**
- `agent/discovery/app_model.py` — typed shapes for Route, Form, XHR, AuthWall,
  Role, ApplicationModel.
- `agent/discovery/cred_vault.py` — run-scoped, in-memory-only credential store
  that auto-clears on run completion.
- `agent/workflows/autonomous_qa.py` — LangGraph workflow with nodes:
  `intake → discover → propose_plan → need_creds? → execute → classify → report`
  The `need_creds` node is a **pause point**: it emits a `NEEDS_CREDS` signal,
  suspends the run, and waits for the user to POST credentials.
- `src/api/routes/autonomous.py` — REST endpoints:
  - `POST /api/v1/auto/start`  body: `{url, scope?, budget?}` → `{run_id, state}`
  - `GET  /api/v1/auto/{run_id}/status` → current state + pending prompts
  - `POST /api/v1/auto/{run_id}/credentials` body: `{role, username, password,
    totp_seed?}` → resumes run
  - `POST /api/v1/auto/{run_id}/cancel`
- UI: dropdown option **"Autonomous QA (URL only)"** + credential modal that
  pops when status returns `NEEDS_CREDS`.

**Done when:** Paste a URL → agent runs a placeholder discovery → pauses asking
for creds if it detects a login wall → user provides creds via modal → run
resumes → emits a report.

---

## Phase 2 — Discovery + Application Model

**Goal:** Turn a URL into a structured model of the application.

**Deliverables:**
- `agent/discovery/crawler.py` — bounded Playwright crawler:
  - Starts at URL, breadth-first, max-depth + max-pages budget
  - Records: route graph, forms (with field types), XHR/fetch calls, HTTP
    status distributions, redirects, auth walls (401/login-form detection),
    static vs dynamic content markers
  - Emits a `DiscoveryEvent` stream (SSE-friendly) so the UI shows live progress
- `agent/discovery/model_builder.py` — consumes crawl data, produces
  `ApplicationModel`: pages, components, API endpoints, data entities,
  detected roles, inferred user journeys
- `agent/discovery/workflow_inference.py` — from the model, proposes:
  - Smoke suite (anonymous: landing loads, static routes 200, no 500s)
  - Inferred journeys (login → dashboard → core-action → logout)
  - API contract suite (every observed XHR → schema test)
  - Visual regression targets (key landing pages)

**Done when:** URL in → `ApplicationModel` + proposed suite out, visible in UI
as a review step before execution.

---

## Phase 3 — Execution Profiles + Auth Plugins

**Goal:** Respect tenant/role/state. Pluggable auth.

**Deliverables:**
- `agent/profiles/execution_profile.py` — bundle `{tenant, role, env, data,
  feature_flags, auth_ref}`
- `agent/auth/base.py` — `AuthPlugin` interface with `detect`, `apply`, `verify`
- `agent/auth/plugins/` — `form_login.py`, `basic_auth.py`, `bearer_token.py`,
  `oauth_redirect.py`, `otp_sms.py` (hooks into existing jiomart_auth pattern)
- Credential vault accepts multiple identities per run and tags each to a role
- Discovery records which pages need which auth → run selects the right
  plugin per journey

**Done when:** An e-commerce site with customer + admin roles is explored and
tested under both identities correctly, with creds collected once via modal.

---

## Phase 4 — Oracle Layer (business correctness)

**Goal:** Move beyond "pytest exit 0" to semantic pass/fail.

**Deliverables:**
- `agent/oracles/universal.py` — always-true rules: no HTTP 500, no console
  errors, no unhandled promise rejections, no security headers missing
- `agent/oracles/inferred.py` — LLM-derived from discovery:
  "cart total should equal sum of line items", "pagination count should match
  record count" — tracked as hypotheses with confidence
- `agent/oracles/configured.py` — tenant rules loaded from DB
- `agent/oracles/confirmed.py` — baselines confirmed by a human, stored
  per-tenant
- `agent/classify.py` — maps findings into `{universal|inferred|configured|
  confirmed}` × `{bug|regression|flake|noise}`

**Done when:** Reports distinguish "this is definitely a bug" (universal)
from "this looks wrong based on how the app behaved elsewhere" (inferred).

---

## Phase 5 — Self-Healing UI + Learned Selector Memory

**Goal:** Flaky selectors no longer break runs. Each run teaches the next.

**Deliverables:**
- `agent/workflows/langgraph_ui_test.py` — LangGraph with generate → run →
  (selector miss?) → relocate → update memory → rerun (up to N heals)
- `agent/memory/selector_memory.py` — SQLite table keyed by `{tenant, url,
  element_semantic}` → `{last_known_selector, attempt_count, last_success_ts}`
- Relocate node uses DOM snapshot + LLM to find element semantically
  ("the primary CTA button under the hero section") and proposes a new
  stable selector (prefers `data-testid`, `aria-label`, accessible name)

**Done when:** A site redesign that renames CSS classes doesn't break the
next run — the agent relocates and updates memory.

---

## Phase 6 — Persistent Run Intelligence + Replay + Baselines

**Goal:** Runs compound in value over time.

**Deliverables:**
- `agent/memory/run_intel.py` — every run writes:
  - discovered app model diff vs last run (drift detection)
  - selector updates
  - oracle hypothesis updates (confidence ± based on outcomes)
  - flake scoring per test
- `agent/workflows/replay.py` — re-run a past run with the same profile,
  seed, and plan; diff outcomes
- `agent/baselines/` — visual + behavioral baselines that can be approved
  by a user; future runs diff against approved baselines
- `agent/regression.py` — given two runs, classify diffs into
  `{intentional_change|regression|flake|noise}`

**Done when:** "Run #42 vs today" returns a diff with regressions highlighted
and noise filtered.

---

## Cross-cutting concerns

- **All phases** route LLM calls through `src/llm/compat.py` so guardrails,
  cost tracking, and tracing work uniformly.
- **All phases** emit progress via `src/agents/progress_bus.py` so the live
  THINKING… badge shows the current stage.
- **All phases** are tenant-scoped via existing tenancy middleware.
- **Checkpointing** uses `LANGGRAPH_CHECKPOINT=sqlite` so long-running
  autonomous runs survive restarts.

---

## State Machine (Phase 1)

```
                    ┌─────────────┐
     POST /start──▶│   RUNNING   │
                   │  discover   │
                   └──────┬──────┘
                          │
                   ┌──────▼──────┐
                   │  NEEDS_CREDS │◀──── GET /status polled by UI
                   │  (paused)   │
                   └──────┬──────┘
                          │ POST /credentials
                   ┌──────▼──────┐
                   │   RUNNING   │
                   │  execute    │
                   └──────┬──────┘
                          │
                   ┌──────▼──────┐
                   │    DONE     │
                   └─────────────┘
```

States: `PENDING | RUNNING | NEEDS_CREDS | DONE | FAILED | CANCELLED`

Credentials are held in an in-memory dict keyed by run_id, wiped on
`DONE|FAILED|CANCELLED`. Never written to disk, never logged, never
returned from any endpoint.
