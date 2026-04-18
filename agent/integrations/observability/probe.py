"""agent/integrations/observability/probe.py — The vendor-neutral probe.

Same closed-loop idea as the original Puvi module, but parameterized by a
``PlatformAdapter``. This is what lets us point at any MLOps / agent-
observability vendor and get meaningful findings without per-vendor code.

Pipeline:
    1.  Adapter picks the ingest path / auth header (from candidates) on
        first successful POST.
    2.  Emit a deterministic trace stream shaped by the adapter.
    3.  Poll the adapter's list endpoint, diff against ground truth.
    4.  Hit the adapter's aggregate endpoint, verify analytics math.
    5.  Open the adapter's dashboard URL, verify UI matches API.
    6.  Persist everything learned to ``platform_profiles`` memory.
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

import requests

from agent.oracles.base import Finding
from agent.integrations.observability.base import (
    BaseAdapter, PlatformAdapter, TracePayload,
)
from agent.integrations.observability import registry
from agent.integrations.puvi.synthetic_agent import TraceRecipe, GroundTruth, EmittedTrace
from agent.integrations.puvi.trace_roundtrip import verify_traces_roundtrip
from agent.integrations.puvi.calculations import verify_aggregates
from agent.integrations.puvi.consistency import verify_ui_matches_api
from agent.integrations.puvi.onboarding import run_onboarding, OnboardingResult
from agent.memory import platform_profiles


@dataclass
class ProbeResult:
    ok: bool
    adapter_name: str
    findings: list[Finding] = field(default_factory=list)
    ground_truth: GroundTruth | None = None
    onboarding: OnboardingResult | None = None
    dashboard_url: str = ""
    working_ingest_path: str = ""


# ── Vendor-neutral emitter ────────────────────────────────────────────────

class AdapterEmitter:
    """Wraps an adapter + api_base + api_key into a callable emit_fn.

    Handles endpoint discovery (walk adapter.ingest_paths) and header
    rotation (walk adapter.auth_header_candidates) until we find a
    combination that Puvi/LangSmith/etc. accepts with 2xx.
    """
    def __init__(self, adapter: PlatformAdapter, *, api_base: str,
                 api_key: str, workspace_id: str = ""):
        self.adapter = adapter
        self.api_base = api_base.rstrip("/")
        self.api_key = api_key
        self.workspace_id = workspace_id
        self.working_path: Optional[str] = None
        self.working_header: Optional[str] = None

    def _headers(self, header_name: str) -> dict:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            if header_name == "Authorization":
                h[header_name] = f"Bearer {self.api_key}"
            else:
                h[header_name] = self.api_key
        return h

    def emit_one(self, tp: TracePayload) -> bool:
        payload = self.adapter.shape_ingest_payload(tp, self.workspace_id)
        paths = [self.working_path] if self.working_path else list(self.adapter.ingest_paths)
        headers_list = ([self.working_header] if self.working_header
                         else list(self.adapter.auth_header_candidates))
        for path in paths:
            for header_name in headers_list:
                try:
                    r = requests.post(f"{self.api_base}{path}",
                                      data=json.dumps(payload),
                                      headers=self._headers(header_name),
                                      timeout=6)
                    if 200 <= r.status_code < 300:
                        self.working_path = path
                        self.working_header = header_name
                        return True
                    if r.status_code in (401, 403):
                        # Auth wrong but endpoint right — lock in the path.
                        self.working_path = path
                except Exception:
                    continue
        return False


# ── Build ground truth + emit ────────────────────────────────────────────

def _emit_stream(adapter: PlatformAdapter, recipe: TraceRecipe, *,
                 api_base: str, api_key: str, agent_id: str,
                 workspace_id: str = "") -> tuple[GroundTruth, str, str]:
    """Generate recipe-shaped synthetic traces and emit through the adapter."""
    import random
    rng = random.Random(recipe.seed)
    session_ids = [f"session-{uuid.uuid4().hex[:8]}"
                   for _ in range(recipe.session_count)]
    emitter = AdapterEmitter(adapter, api_base=api_base, api_key=api_key,
                              workspace_id=workspace_id)
    emitted: list[EmittedTrace] = []
    delivered = 0

    for _ in range(recipe.total_traces):
        tool = rng.choices(recipe.tools, weights=recipe.tool_weights, k=1)[0]
        model = rng.choice(recipe.model_names)
        latencies, probs = zip(*recipe.latency_ms_tiers)
        base_latency = rng.choices(latencies, weights=probs, k=1)[0]
        latency = max(1, int(base_latency * rng.uniform(0.7, 1.4)))
        is_error = rng.random() < recipe.error_rate
        error_kind = rng.choice(recipe.error_kinds) if is_error else ""
        pt = rng.randint(*recipe.prompt_tokens_range)
        ct = rng.randint(*recipe.completion_tokens_range)
        session = rng.choice(session_ids)
        trace_id = uuid.uuid4().hex
        tp = TracePayload(
            trace_id=trace_id, agent_id=agent_id, session_id=session,
            model=model, tool=tool, latency_ms=latency,
            is_error=is_error, error_kind=error_kind,
            prompt_tokens=pt, completion_tokens=ct,
            timestamp=time.time(),
        )
        ok = emitter.emit_one(tp)
        if ok:
            delivered += 1
        emitted.append(EmittedTrace(
            trace_id=trace_id, agent_id=agent_id, session_id=session,
            model=model, tool=tool, latency_ms=latency,
            is_error=is_error, error_kind=error_kind,
            prompt_tokens=pt, completion_tokens=ct,
            timestamp=tp.timestamp,
            raw_payload=adapter.shape_ingest_payload(tp, workspace_id),
        ))
        time.sleep(0.02)

    gt = GroundTruth(agent_id=agent_id, traces=emitted)
    setattr(gt, "delivery_stats",
            {"attempted": recipe.total_traces,
             "accepted_by_ingest": delivered})
    return gt, emitter.working_path or "", emitter.working_header or ""


# ── Top-level entry ──────────────────────────────────────────────────────

def run_observability_probe(browser_context, *, base_url: str,
                            signup_url: str, email: str, password: str,
                            adapter_name: str = "",
                            sample_text: str = "",
                            recipe: TraceRecipe | None = None,
                            tenant_id: str = "default",
                            dashboard_url_template: str = "",
                            ) -> ProbeResult:
    """Vendor-neutral closed-loop probe.

    ``adapter_name`` — explicit vendor choice (from UI dropdown). Empty
    string triggers heuristic detection from ``sample_text`` (page text
    gathered during discovery) + the base URL host.
    """
    adapter = (registry.get(adapter_name) if adapter_name
               else registry.detect(base_url, sample_text))
    findings: list[Finding] = []

    # 1. Onboarding.
    onb = run_onboarding(browser_context, base_url=base_url,
                         signup_url=signup_url, email=email,
                         password=password)
    findings.extend(onb.findings)
    if not onb.ok or not onb.api_key:
        return ProbeResult(ok=False, adapter_name=adapter.name,
                           findings=findings, onboarding=onb)

    # 2. Emit stream.
    profile = platform_profiles.load(tenant_id, base_url)
    recipe = recipe or TraceRecipe()
    agent_id = f"qa-agent-{uuid.uuid4().hex[:8]}"
    gt, working_path, working_header = _emit_stream(
        adapter, recipe,
        api_base=onb.ingest_url or base_url,
        api_key=onb.api_key, agent_id=agent_id,
        workspace_id=onb.workspace_id,
    )

    # 3-5. Existing oracles work unchanged — they already take
    #      GroundTruth + base_url + api_key.
    roundtrip_findings, listed = verify_traces_roundtrip(
        gt, base_url=base_url, api_key=onb.api_key)
    findings.extend(roundtrip_findings)
    findings.extend(verify_aggregates(
        gt, base_url=base_url, api_key=onb.api_key, listed_traces=listed))
    dashboard_url = (
        dashboard_url_template
        or profile.get("dashboard_tpl", "").format(agent_id=agent_id)
        or f"{base_url.rstrip('/')}{adapter.dashboard_tpl.format(agent_id=agent_id)}"
    )
    findings.extend(verify_ui_matches_api(
        browser_context, dashboard_url=dashboard_url, gt=gt))

    # 6. Persist learned knowledge keyed to this platform.
    try:
        accuracy = _score_accuracy(gt, findings)
        patch = {
            "ingest_path": working_path,
            "auth_header": working_header,
            "dashboard_tpl": adapter.dashboard_tpl,
            "last_accuracy": accuracy,
            "last_run_emitted": gt.total,
            "adapter_name": adapter.name,
        }
        patch = {k: v for k, v in patch.items() if v}
        if patch:
            platform_profiles.update(tenant_id, base_url,
                                     platform=adapter.name, patch=patch)
    except Exception:
        pass

    return ProbeResult(
        ok=True, adapter_name=adapter.name, findings=findings,
        ground_truth=gt, onboarding=onb, dashboard_url=dashboard_url,
        working_ingest_path=working_path,
    )


def _score_accuracy(gt: GroundTruth, findings: list[Finding]) -> float:
    if gt.total == 0:
        return 0.0
    penalties = 0.0
    for f in findings:
        src = getattr(f, "source", "")
        if src.startswith("puvi.") or src.startswith("observability."):
            if f.severity == "confirmed":
                penalties += 0.2
            elif f.severity == "universal":
                penalties += 0.08
            elif f.severity == "configured":
                penalties += 0.04
    return max(0.0, 1.0 - penalties)
