"""agent/workflows/autonomous_qa.py — Autonomous QA run driver.

This is the Phase 1 orchestrator. A run progresses through a state machine
that can pause mid-way to ask the user for credentials via the REST API,
then resume when the user provides them.

States:
    PENDING       — created, not yet started
    RUNNING       — worker thread is actively doing work
    NEEDS_CREDS   — paused; user action required (POST /credentials)
    DONE          — completed successfully
    FAILED        — errored out
    CANCELLED     — user cancelled

The actual heavy lifting (discovery, test generation, execution) is delegated
to dedicated modules — `agent/discovery/crawler.py` for Phase 2, later phases
will add execution nodes, oracle nodes, classification nodes. This module
owns the STATE MACHINE and PAUSE SEMANTICS; nothing else.

Threading model:
    Each autonomous run spawns exactly one worker thread (daemon). The worker
    does blocking I/O (Playwright, network, LLM). Control signals between the
    API layer and the worker flow through a threading.Event (resume) and
    threading.Event (cancel). Results + transient state are stored in a
    RunContext held in a module-level registry.
"""
from __future__ import annotations

import os
import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from agent.discovery import cred_vault
from agent.discovery.app_model import ApplicationModel


# ── State enum ──────────────────────────────────────────────────────────────

class State:
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    NEEDS_CREDS = "NEEDS_CREDS"
    DONE = "DONE"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


TERMINAL = {State.DONE, State.FAILED, State.CANCELLED}


# ── Run context ─────────────────────────────────────────────────────────────

@dataclass
class RunContext:
    run_id: str
    url: str
    state: str = State.PENDING
    stage: str = "queued"                   # human-readable current step
    events: list[dict] = field(default_factory=list)   # progress log (tail-readable)
    model: Optional[ApplicationModel] = None
    findings: list[dict] = field(default_factory=list)
    error: str = ""
    started_at: float = 0.0
    finished_at: float = 0.0
    scope: dict = field(default_factory=dict)     # e.g. {"max_pages": 30}

    # Internal — not serialized
    _thread: Optional[threading.Thread] = None
    _resume_event: Optional[threading.Event] = None
    _cancel_event: Optional[threading.Event] = None

    def to_status(self) -> dict:
        """Safe-to-return status snapshot (no creds, no threads)."""
        # Extract the latest classification + plan_summary events so the UI
        # can render oracle severity breakdowns and step kind counts without
        # scanning the whole event log itself.
        classification = None
        plan_summary = None
        for ev in reversed(self.events):
            stg = ev.get("stage")
            if classification is None and stg == "classification":
                classification = {k: v for k, v in ev.items()
                                  if k not in ("ts", "stage")}
            elif plan_summary is None and stg == "plan_summary":
                plan_summary = {k: v for k, v in ev.items()
                                if k not in ("ts", "stage")}
            if classification and plan_summary:
                break

        # Count findings by severity for a quick UI glance.
        sev_counts: dict[str, int] = {}
        for f in self.findings:
            sev = f.get("severity", "noise")
            sev_counts[sev] = sev_counts.get(sev, 0) + 1

        return {
            "run_id": self.run_id,
            "url": self.url,
            "state": self.state,
            "stage": self.stage,
            "events": self.events[-50:],
            "findings_count": len(self.findings),
            "findings_by_severity": sev_counts,
            "classification": classification,
            "plan_summary": plan_summary,
            "pending_prompts": cred_vault.pending_prompts(self.run_id),
            "model_summary": (
                {
                    "pages": len(self.model.routes),
                    "auth_walls": len(self.model.auth_walls()),
                    "api_endpoints": len(self.model.api_endpoints),
                    "fingerprint": self.model.fingerprint(),
                }
                if self.model else None
            ),
            "error": self.error,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


# ── Global registry ────────────────────────────────────────────────────────

_LOCK = threading.RLock()
_RUNS: dict[str, RunContext] = {}


def _log(ctx: RunContext, stage: str, **extra: Any) -> None:
    ctx.stage = stage
    ctx.events.append({"ts": time.time(), "stage": stage, **extra})
    # Mirror to the SSE progress bus if wired.
    try:
        from src.agents import progress_bus
        progress_bus.emit("auto_stage", stage)
    except Exception:
        pass


# ── Public API ─────────────────────────────────────────────────────────────

def start_run(url: str, scope: Optional[dict] = None) -> RunContext:
    """Create a new autonomous run and kick off its worker thread."""
    run_id = uuid.uuid4().hex[:12]
    ctx = RunContext(
        run_id=run_id,
        url=url,
        scope=scope or {},
        _resume_event=threading.Event(),
        _cancel_event=threading.Event(),
    )
    with _LOCK:
        _RUNS[run_id] = ctx
    cred_vault.start_bucket(run_id)

    t = threading.Thread(target=_worker, args=(ctx,), daemon=True,
                         name=f"auto-run-{run_id}")
    ctx._thread = t
    ctx.started_at = time.time()
    ctx.state = State.RUNNING
    t.start()
    return ctx


def get_run(run_id: str) -> Optional[RunContext]:
    with _LOCK:
        return _RUNS.get(run_id)


def list_runs() -> list[dict]:
    with _LOCK:
        return [ctx.to_status() for ctx in _RUNS.values()]


def provide_credentials(run_id: str, role: str, username: str,
                        password: str, totp_seed: str = "",
                        extras: Optional[dict] = None) -> bool:
    """Called by POST /credentials. Stores creds in the vault and signals
    the worker to resume."""
    ctx = get_run(run_id)
    if not ctx or ctx.state in TERMINAL:
        return False
    cred_vault.set_credential(run_id, cred_vault.Credential(
        role=role, username=username, password=password,
        totp_seed=totp_seed, extras=extras or {},
    ))
    # If no more prompts are pending, unblock the worker.
    if not cred_vault.pending_prompts(run_id):
        if ctx._resume_event:
            ctx._resume_event.set()
        ctx.state = State.RUNNING
        _log(ctx, "credentials_received", role=role)
    else:
        _log(ctx, "credentials_partial", role=role,
             still_needed=[p["role"] for p in cred_vault.pending_prompts(run_id)])
    return True


def cancel_run(run_id: str) -> bool:
    ctx = get_run(run_id)
    if not ctx or ctx.state in TERMINAL:
        return False
    if ctx._cancel_event:
        ctx._cancel_event.set()
    if ctx._resume_event:
        ctx._resume_event.set()   # unblock the worker so it can notice cancel
    return True


# ── Worker ─────────────────────────────────────────────────────────────────

def _worker(ctx: RunContext) -> None:
    """Single worker thread that drives one run through the state machine."""
    try:
        _log(ctx, "discovering", url=ctx.url)
        ctx.model = _run_discovery(ctx)

        if ctx._cancel_event and ctx._cancel_event.is_set():
            _finalize(ctx, State.CANCELLED)
            return

        # Phase 1 pause point: if discovery found an auth wall, request creds
        # for each role and wait.
        if ctx.model and ctx.model.needs_credentials():
            for role in (ctx.model.roles or []):
                if not cred_vault.has_credential(ctx.run_id, role.name):
                    cred_vault.request_credential(
                        ctx.run_id, role.name,
                        hint=f"Auth wall detected at {role.discovered_at}",
                        page_url=role.discovered_at,
                    )
            if cred_vault.pending_prompts(ctx.run_id):
                ctx.state = State.NEEDS_CREDS
                _log(ctx, "needs_credentials",
                     roles=[p["role"] for p in cred_vault.pending_prompts(ctx.run_id)])
                # Wait (bounded by 1 hour) for creds or cancel.
                waited = ctx._resume_event.wait(timeout=3600) if ctx._resume_event else False
                if not waited or (ctx._cancel_event and ctx._cancel_event.is_set()):
                    _finalize(ctx, State.CANCELLED if waited else State.FAILED,
                              error="credential_wait_timeout" if not waited else "")
                    return
                ctx.state = State.RUNNING

        _log(ctx, "generating_plan")
        plan = _propose_plan(ctx)

        _log(ctx, "executing", suite_size=len(plan))
        findings = _execute_plan(ctx, plan)
        ctx.findings = findings

        _log(ctx, "classifying")
        _classify_findings(ctx)

        _log(ctx, "reporting")
        _write_report(ctx)

        _finalize(ctx, State.DONE)

    except Exception as e:
        tb = traceback.format_exc()
        _log(ctx, "error", error=str(e), traceback=tb[-2000:])
        _finalize(ctx, State.FAILED, error=str(e))


# ── Phase-delegated helpers (stubs to be fleshed out per phase) ────────────

def _run_discovery(ctx: RunContext) -> ApplicationModel:
    """Phase 2 entry point — crawl the site + enrich the model.

    Replay mode short-circuits the crawl and rehydrates a snapshot instead
    (see agent/workflows/replay.py)."""
    snapshot = (ctx.scope or {}).get("snapshot_model")
    if snapshot:
        _log(ctx, "replay_from_snapshot", run_id=ctx.scope.get("replay_of"))
        return ApplicationModel.from_dict(snapshot)

    from agent.discovery.crawler import crawl
    from agent.discovery.model_builder import enrich

    def _on_event(evt):
        _log(ctx, f"crawl:{evt.get('kind','?')}", **{
            k: v for k, v in evt.items() if k != "kind"
        })

    raw_model = crawl(
        ctx.url,
        max_pages=ctx.scope.get("max_pages"),
        max_depth=ctx.scope.get("max_depth"),
        on_event=_on_event,
    )
    # Phase 2 enrichment: cluster roles, tag purposes, group API domains.
    return enrich(raw_model)


def _propose_plan(ctx: RunContext) -> list[dict]:
    """Phase 2 suite inference driven by the enriched model."""
    if not ctx.model:
        return []
    from agent.discovery.workflow_inference import propose_suite, summarize_suite
    steps = propose_suite(ctx.model)
    _log(ctx, "plan_summary", **summarize_suite(steps))
    return steps


def _build_profiles(ctx: RunContext):
    """Phase 3: build an ExecutionProfile for every role the model needs."""
    from agent.profiles import profile_for_role
    profiles = [profile_for_role("anonymous")]
    if ctx.model:
        for role in ctx.model.roles:
            profiles.append(profile_for_role(role.name,
                                              auth_plugin=role.auth_plugin or "form_login"))
    return profiles


def _run_oracles(ctx: RunContext, tenant_id: str):
    """Phase 4: emit oracle findings (universal + inferred + configured + confirmed)."""
    if not ctx.model:
        return []
    from agent.oracles.universal import run_universal
    from agent.oracles.inferred import run_inferred
    from agent.oracles.configured import run_configured
    from agent.oracles.confirmed import run_confirmed
    findings = []
    findings.extend(run_universal(ctx.model))
    findings.extend(run_inferred(ctx.model, tenant_id=tenant_id))
    findings.extend(run_configured(ctx.model, tenant_id=tenant_id))
    findings.extend(run_confirmed(ctx.model, tenant_id=tenant_id))
    return findings


def _authenticate(ctx: RunContext, profile, browser_ctx=None):
    """Phase 3: select an auth plugin for this profile and apply it.
    Returns an AuthResult (or None if anonymous / no plugin matched)."""
    if not profile.requires_auth():
        return None
    cred = cred_vault.get_credential(ctx.run_id, profile.cred_ref or profile.role)
    if not cred:
        _log(ctx, "auth_skipped_no_creds", role=profile.role)
        return None
    from agent.auth.registry import get as get_plugin, best_for
    from agent.auth.base import AuthResult

    detected_forms = []
    page_url = profile.name  # placeholder when no crawl data
    # Find the auth wall route for this role in the model.
    if ctx.model:
        for r in ctx.model.routes:
            if r.is_auth_wall and (r.requires_role == profile.role or not r.requires_role):
                page_url = r.url
                detected_forms = [{
                    "selector": f.selector, "action": f.action, "method": f.method,
                    "fields": [ff.__dict__ for ff in f.fields],
                } for f in r.forms]
                break

    auth_ctx = {
        "page_url": page_url,
        "page_title": "",
        "detected_forms": detected_forms,
        "status_code": 401 if not detected_forms else 200,
        "response_headers": {},
        "credential": cred,
        "browser_context": browser_ctx,
        "http_session": None,
        "app_origin": ctx.url,
    }
    # Prefer the plugin the role declared; fall back to best-effort detect.
    plugin = get_plugin(profile.auth_plugin) or best_for(auth_ctx)
    if not plugin:
        _log(ctx, "auth_no_plugin", role=profile.role)
        return None
    _log(ctx, "auth_attempt", role=profile.role, plugin=plugin.name)
    result = plugin.apply(auth_ctx)
    _log(ctx, "auth_result", role=profile.role, plugin=plugin.name,
         ok=result.ok, message=result.message)
    return result


def _maybe_run_puvi_probe(ctx: RunContext, findings: list[dict],
                          get_browser_ctx) -> None:
    """Run the vendor-neutral observability probe when the target looks
    like (or is declared to be) an MLOps / agent-observability platform.

    Activation:
      * ``scope.platform_type`` is in the observability registry, OR
      * ``scope.platform_type == "puvi"`` (explicit legacy), OR
      * the discovery model's sampled page text / host matches any
        registered adapter's heuristics.

    The probe onboards a synthetic agent, emits a deterministic trace
    stream shaped for the chosen vendor, and verifies round-trip,
    analytics math, and UI/API consistency. Works on Puvi, LangSmith,
    Langfuse, Arize Phoenix, and any registered vendor — plus a generic
    fallback for unknown platforms.
    """
    from agent.integrations.observability import registry as adapter_registry
    from agent.integrations.observability.probe import run_observability_probe
    from agent.integrations.puvi.synthetic_agent import TraceRecipe

    scope = ctx.scope or {}
    declared = scope.get("platform_type") or ""

    # Gather a bit of page text for heuristic detection.
    sample = ""
    if ctx.model:
        for r in ctx.model.routes[:30]:
            sample += " " + (r.title or "")
            sample += " " + " ".join(r.headings or [])

    detected = adapter_registry.detect(ctx.url, sample)
    # UI sentinel: "__observability__" means "run the probe, let the backend
    # pick the right vendor adapter" — we don't expose vendor names in the UI.
    force_observability = declared in ("__observability__", "observability")
    is_observability = (
        force_observability
        or declared in {a["name"] for a in adapter_registry.list_adapters()}
        or (detected.name != "generic" and declared == "")
    )
    if not is_observability:
        return

    # Resolve the actual adapter: explicit vendor wins, otherwise fall back
    # to auto-detection from URL + page content.
    if declared and declared not in ("__observability__", "observability",
                                       "generic"):
        adapter_name = declared
    else:
        adapter_name = detected.name if detected.name != "generic" else ""
    _log(ctx, "observability_probe_start", adapter=adapter_name)

    # Reserve role name: same across vendors — the UI prompts for signup
    # creds under this role.
    cred_role = scope.get("signup_role") or "observability_signup"
    cred = cred_vault.get_credential(ctx.run_id, cred_role)
    if not cred:
        # Back-compat: some older runs used 'puvi_signup'.
        cred = cred_vault.get_credential(ctx.run_id, "puvi_signup")
    if not cred:
        _log(ctx, "observability_probe_skipped_no_creds",
             needed_role=cred_role)
        findings.append({
            "source": "observability.workflow", "severity": "inferred",
            "kind": "noise",
            "title": f"{adapter_name} probe skipped (no signup creds supplied)",
            "passed": True,
            "detail": f"Provide a credential under role '{cred_role}' in "
                      f"the UI to activate the closed-loop probe.",
        })
        return

    browser_ctx = get_browser_ctx()
    if not browser_ctx:
        _log(ctx, "observability_probe_no_browser")
        return

    signup_url = scope.get("signup_url") or f"{ctx.url.rstrip('/')}/signup"
    recipe = TraceRecipe(
        total_traces=int(scope.get("probe_trace_count", 50)),
        seed=int(scope.get("probe_seed", 42)),
    )
    tenant_id = scope.get("tenant_id", "default")

    result = run_observability_probe(
        browser_ctx, base_url=ctx.url, signup_url=signup_url,
        email=cred.username, password=cred.password,
        adapter_name=adapter_name, sample_text=sample,
        recipe=recipe, tenant_id=tenant_id,
    )
    for f in result.findings:
        d = f.to_dict() if hasattr(f, "to_dict") else dict(f)
        d.setdefault("source", f"observability.{result.adapter_name}")
        d.setdefault("passed", d.get("severity") in ("noise", "inferred"))
        findings.append(d)
    _log(ctx, "observability_probe_done",
         adapter=result.adapter_name,
         finding_count=len(result.findings),
         emitted=result.ground_truth.total if result.ground_truth else 0,
         working_ingest_path=result.working_ingest_path,
         api_key_captured=bool(result.onboarding and result.onboarding.api_key))


def _execute_plan(ctx: RunContext, plan: list[dict]) -> list[dict]:
    """Phase 3+ executor: routes steps through the right profile/session.

    Keeps HTTP-level steps fast (requests.Session) and only spins up a
    Playwright browser when an authenticated-UI step or visual.baseline
    step actually needs one.
    """
    import requests
    findings: list[dict] = []
    sess = requests.Session()
    sess.headers.update({
        "User-Agent": os.getenv("AUTO_USER_AGENT",
                                "Mozilla/5.0 (QAAgent) Autonomous-Exec/1.0"),
    })
    profiles = _build_profiles(ctx)
    # Lazy browser — only created if a step needs it.
    browser_ctx_box: dict = {"ctx": None, "pw": None, "browser": None}

    def _get_browser_ctx():
        if browser_ctx_box["ctx"] is not None:
            return browser_ctx_box["ctx"]
        try:
            from playwright.sync_api import sync_playwright
            pw = sync_playwright().start()
            browser = pw.chromium.launch(headless=True)
            browser_ctx = browser.new_context(ignore_https_errors=True)
            browser_ctx_box.update({"ctx": browser_ctx, "pw": pw, "browser": browser})
            return browser_ctx
        except Exception as e:
            _log(ctx, "browser_unavailable", error=str(e))
            return None

    # Authenticate each role once up-front and store the AuthResult.
    from agent.profiles import pick_profile
    auth_results: dict[str, object] = {}
    for p in profiles:
        if p.requires_auth():
            res = _authenticate(ctx, p, browser_ctx=_get_browser_ctx())
            if res:
                auth_results[p.role] = res
                # Attach cookies/headers to the shared HTTP session so API
                # contract replays run as the authenticated user.
                try:
                    for k, v in getattr(res, "cookies", {}).items():
                        sess.cookies.set(k, v)
                    sess.headers.update(getattr(res, "headers", {}) or {})
                except Exception:
                    pass

    tenant_id = (ctx.scope or {}).get("tenant_id", "default")

    for i, step in enumerate(plan):
        if ctx._cancel_event and ctx._cancel_event.is_set():
            break
        kind = step.get("kind")
        prof = pick_profile(step, profiles) or profiles[0]
        step_key = f"{kind}::{step.get('url','')}::{prof.role}"
        try:
            if kind == "smoke.page_loads":
                r = sess.get(step["url"], timeout=15, allow_redirects=True)
                ok = r.status_code <= step.get("expect_status_max", 499)
                findings.append({
                    "step_index": i, "kind": kind, "url": step["url"],
                    "status_code": r.status_code, "passed": ok,
                    "role": prof.role,
                    "severity": "universal" if (not ok and r.status_code >= 500) else "noise",
                    "source": f"step:{i}",
                    "title": f"{kind} {step['url']}",
                })

            elif kind == "api.contract_replay":
                r = sess.request(step["method"], step["url"], timeout=15)
                klass = r.status_code // 100
                ok = klass == step.get("expect_status_class", 2)
                findings.append({
                    "step_index": i, "kind": kind,
                    "method": step["method"], "url": step["url"],
                    "status_code": r.status_code, "passed": ok,
                    "role": prof.role,
                    "severity": "universal" if (not ok and klass == 5) else "inferred",
                    "source": f"step:{i}",
                    "title": f"{kind} {step['method']} {step['url']}",
                })

            elif kind == "auth.login_smoke":
                # Did auth actually succeed earlier?
                res = auth_results.get(step.get("role"))
                ok = bool(res and getattr(res, "ok", False))
                findings.append({
                    "step_index": i, "kind": kind, "role": step["role"],
                    "passed": ok, "url": step.get("landing_url", ""),
                    "plugin": step.get("plugin", ""),
                    "severity": "universal" if not ok else "noise",
                    "source": f"step:{i}",
                    "title": f"auth:{step['role']}",
                    "detail": getattr(res, "message", "") if res else "no auth attempt",
                })

            elif kind == "journey.walk":
                # Walk a set of URLs under the role's session; any 5xx fails.
                walk_ok = True
                last_status = 0
                for u in step.get("urls", []):
                    r = sess.get(u, timeout=15, allow_redirects=True)
                    last_status = r.status_code
                    if r.status_code >= 500:
                        walk_ok = False
                        break
                findings.append({
                    "step_index": i, "kind": kind, "url": step.get("name", ""),
                    "passed": walk_ok, "role": step.get("role", "anonymous"),
                    "severity": "universal" if not walk_ok else "noise",
                    "source": f"step:{i}",
                    "title": f"journey:{step.get('name','')}",
                    "evidence": {"last_status": last_status,
                                 "url_count": len(step.get("urls", []))},
                })

            elif kind == "visual.baseline":
                # Capture + either diff against an existing baseline or propose one.
                browser_ctx = _get_browser_ctx()
                passed = True
                detail = ""
                if browser_ctx is not None:
                    from agent.baselines.visual import capture_visual_baseline
                    from agent.oracles.confirmed import diff_against
                    b = capture_visual_baseline(browser_ctx, step["url"],
                                                 scope=step["url"],
                                                 tenant_id=tenant_id)
                    if b:
                        drift = diff_against(b.hash, b.scope, tenant_id=tenant_id)
                        if drift:
                            passed = False
                            detail = drift[0].title
                        else:
                            detail = f"captured hash={b.hash[:12]}"
                else:
                    detail = "browser unavailable — baseline skipped"
                    passed = True
                findings.append({
                    "step_index": i, "kind": kind, "url": step["url"],
                    "passed": passed, "role": "anonymous",
                    "severity": "confirmed" if not passed else "noise",
                    "source": f"step:{i}", "title": f"visual:{step['url']}",
                    "detail": detail,
                })

            else:
                findings.append({"step_index": i, "kind": kind,
                                 "passed": False, "severity": "noise",
                                 "source": f"step:{i}",
                                 "title": f"unknown:{kind}",
                                 "detail": "unknown step kind"})

        except Exception as e:
            findings.append({"step_index": i, "kind": kind, "passed": False,
                             "severity": "universal", "error": str(e),
                             "source": f"step:{i}",
                             "title": f"{kind} error"})

        # Update flake score for this step key (Phase 6 intel).
        try:
            from agent.memory import run_intel
            run_intel.update_flake(tenant_id, step_key,
                                   failed=not findings[-1].get("passed", False))
        except Exception:
            pass

        _log(ctx, "step_done", index=i, kind=kind,
             passed=findings[-1].get("passed"))

    # ── Platform-specific overlays ─────────────────────────────────────
    # If scope flags this as a Puvi-Labs-style MLOps/observability app
    # (or the crawled model heuristically looks like one), run the
    # closed-loop probe on top of the standard plan. The probe onboards
    # a synthetic agent, emits a deterministic trace stream, and verifies
    # Puvi reported counts/aggregates/UI values correctly.
    try:
        _maybe_run_puvi_probe(ctx, findings, _get_browser_ctx)
    except Exception as e:
        _log(ctx, "puvi_probe_error", error=str(e))

    # Tear down browser if we started one.
    try:
        if browser_ctx_box["browser"]:
            browser_ctx_box["browser"].close()
        if browser_ctx_box["pw"]:
            browser_ctx_box["pw"].stop()
    except Exception:
        pass

    return findings


def _classify_findings(ctx: RunContext) -> None:
    """Phase 4 classifier: severity buckets + critical/attention/signals."""
    from agent.classify import classify, record_hypothesis_outcomes, max_severity
    from agent.oracles.base import Finding

    # Run oracles against the model and merge into the finding list.
    tenant_id = (ctx.scope or {}).get("tenant_id", "default")
    oracle_findings = _run_oracles(ctx, tenant_id)

    # Convert the step-level dicts + oracle Finding objects into a uniform
    # list of dicts for persistence + diffing.
    all_findings: list[dict] = []
    for f in ctx.findings:
        all_findings.append(f)
    for of in oracle_findings:
        all_findings.append(of.to_dict())

    # Build a Finding list for the classifier (ignore legacy step dicts w/o
    # the required fields).
    typed: list[Finding] = []
    for f in all_findings:
        typed.append(Finding(
            source=f.get("source", ""),
            severity=f.get("severity", "noise"),
            kind=f.get("kind", "bug" if not f.get("passed", True) else "noise"),
            title=f.get("title", ""),
            detail=f.get("detail", f.get("error", "")),
            url=f.get("url", ""),
            evidence=f.get("evidence", {}) or {},
            confidence=float(f.get("confidence", 1.0)),
            oracle=f.get("oracle", ""),
        ))
    summary = classify(typed)
    ctx.findings = [t.to_dict() for t in typed]

    # Feed hypothesis outcomes back into the inferred store.
    record_hypothesis_outcomes(typed)

    ctx.events.append({
        "ts": time.time(), "stage": "classification",
        **summary,
        "max_severity": max_severity(typed),
    })


def _write_report(ctx: RunContext) -> None:
    """Dump a JSON report and record run intelligence for Phase 6 diffing."""
    import json
    from pathlib import Path
    import datetime as dt

    out_dir = Path("data/logs")
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = dt.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    out_path = out_dir / f"autonomous_run_{ctx.run_id}_{ts}.json"
    payload = {
        "run_id": ctx.run_id,
        "url": ctx.url,
        "state": ctx.state,
        "model": ctx.model.to_dict() if ctx.model else None,
        "findings": ctx.findings,
        "events": ctx.events,
        "started_at": ctx.started_at,
    }
    out_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    ctx.events.append({"ts": time.time(), "stage": "report_written",
                       "path": str(out_path)})

    # Phase 6: persist run intelligence so future runs can diff.
    try:
        from agent.memory import run_intel
        from agent.classify import max_severity
        from agent.oracles.base import Finding

        tenant_id = (ctx.scope or {}).get("tenant_id", "default")
        typed = [Finding(
            source=f.get("source", ""), severity=f.get("severity", "noise"),
            kind=f.get("kind", "noise"), title=f.get("title", ""),
            detail=f.get("detail", ""), url=f.get("url", ""),
            evidence=f.get("evidence", {}) or {},
            confidence=float(f.get("confidence", 1.0)),
            oracle=f.get("oracle", ""),
        ) for f in ctx.findings]
        run_intel.record_run(
            ctx.run_id, tenant_id=tenant_id, url=ctx.url,
            model_fingerprint=ctx.model.fingerprint() if ctx.model else "",
            state=ctx.state,
            max_severity=max_severity(typed),
            findings_count=len(ctx.findings),
            started_at=ctx.started_at,
            finished_at=time.time(),
        )
        run_intel.record_findings(ctx.run_id, ctx.findings)
        if ctx.model:
            d = ctx.model.to_dict()
            d["fingerprint"] = ctx.model.fingerprint()
            run_intel.record_model_snapshot(ctx.run_id, tenant_id, d)
    except Exception as e:
        ctx.events.append({"ts": time.time(), "stage": "intel_skipped",
                           "error": str(e)})


def _finalize(ctx: RunContext, state: str, error: str = "") -> None:
    ctx.state = state
    ctx.error = error
    ctx.finished_at = time.time()
    _log(ctx, f"finalized:{state}", error=error)
    # Wipe credentials for this run — they must not outlive it.
    cred_vault.clear(ctx.run_id)
