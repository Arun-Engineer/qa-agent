"""agent/integrations/puvi/workflow.py — Top-level Puvi test orchestrator.

Call ``test_puvi_platform(base_url, signup_url, email, password)`` to run
the full closed-loop probe:

    onboarding  →  emit synthetic traces  →  round-trip diff
                →  aggregate-math diff    →  UI/API consistency diff

Everything is returned as a list of ``Finding`` objects, ready to be
merged into the autonomous run's classification pipeline (Phase 4).

Designed to slot into ``autonomous_qa._execute_plan`` when the scope
flag ``platform_type == "puvi"`` is set, or when the discovery model
heuristically detects Puvi (onboarding copy contains "trace",
"observability", "agent registry" etc.).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from agent.oracles.base import Finding
from agent.integrations.puvi.synthetic_agent import (
    SyntheticAgent, TraceRecipe, GroundTruth,
)
from agent.integrations.puvi.onboarding import run_onboarding, OnboardingResult
from agent.integrations.puvi.trace_roundtrip import verify_traces_roundtrip
from agent.integrations.puvi.calculations import verify_aggregates
from agent.integrations.puvi.consistency import verify_ui_matches_api


@dataclass
class PuviTestResult:
    ok: bool
    findings: list[Finding] = field(default_factory=list)
    ground_truth: GroundTruth | None = None
    onboarding: OnboardingResult | None = None
    dashboard_url: str = ""


# Heuristic keywords — if a crawl's page text contains enough of these we
# treat the app as Puvi-like and activate this workflow automatically.
_PUVI_SIGNALS = [
    "agent registry", "agent observability", "trace",
    "ingest traces", "behavioral intelligence", "agent metrics",
    "llm observability", "tool call", "session replay",
]


def looks_like_puvi(sample_text: str) -> bool:
    """Cheap detector for auto-activating this workflow from discovery."""
    t = (sample_text or "").lower()
    hits = sum(1 for kw in _PUVI_SIGNALS if kw in t)
    return hits >= 3


def test_puvi_platform(browser_context, *, base_url: str, signup_url: str,
                       email: str, password: str,
                       dashboard_url_template: str = "",
                       recipe: TraceRecipe | None = None,
                       tenant_id: str = "default",
                       ) -> PuviTestResult:
    """Run the closed-loop Puvi probe, using any prior learned profile as
    a starting belief and persisting newly-learned facts at the end.

    Profile fields we persist over runs:
      * ``ingest_path``        — the first path that returned 2xx on POST
      * ``list_path``          — trace listing endpoint that actually worked
      * ``agg_path``           — aggregate/metrics endpoint that worked
      * ``auth_header``        — which Authorization convention Puvi honors
      * ``dashboard_tpl``      — URL template that renders the agent dash
      * ``last_accuracy``      — how close reported aggregates were to GT
      * ``typical_ingest_ms``  — rough p50 of accepted-ingest RTT
    """
    from agent.memory import platform_profiles

    findings: list[Finding] = []
    profile = platform_profiles.load(tenant_id, base_url)

    # 1. Onboarding — get an API key.
    onb = run_onboarding(browser_context, base_url=base_url,
                         signup_url=signup_url, email=email,
                         password=password)
    findings.extend(onb.findings)
    if not onb.ok or not onb.api_key:
        return PuviTestResult(ok=False, findings=findings, onboarding=onb)

    # 2. Emit synthetic traces — prime with the known-good ingest path if we
    #    remember one from a prior run.
    recipe = recipe or TraceRecipe()
    agent = SyntheticAgent(api_base=onb.ingest_url or base_url,
                           api_key=onb.api_key)
    if profile.get("ingest_path"):
        agent._working_endpoint = profile["ingest_path"]
    gt = agent.emit(recipe)

    # 3. Round-trip — every emitted trace must come back.
    roundtrip_findings, listed = verify_traces_roundtrip(
        gt, base_url=base_url, api_key=onb.api_key,
    )
    findings.extend(roundtrip_findings)

    # 4. Aggregate math.
    findings.extend(verify_aggregates(
        gt, base_url=base_url, api_key=onb.api_key,
        listed_traces=listed,
    ))

    # 5. UI/API consistency.
    dashboard_url = (dashboard_url_template
                     or profile.get("dashboard_tpl", "").format(
                         agent_id=agent.agent_id)
                     or f"{base_url.rstrip('/')}/agents/{agent.agent_id}")
    findings.extend(verify_ui_matches_api(
        browser_context, dashboard_url=dashboard_url, gt=gt,
    ))

    # 6. Persist what we just learned. This is the "agent gets smarter" step.
    try:
        accuracy = _score_accuracy(gt, findings)
        patch: dict = {
            "ingest_path": agent._working_endpoint,
            "dashboard_tpl": dashboard_url.replace(agent.agent_id,
                                                    "{agent_id}"),
            "last_accuracy": accuracy,
            "last_run_emitted": gt.total,
            "last_run_error_findings": sum(1 for f in findings
                                             if f.severity == "confirmed"),
        }
        # Drop any key whose value is falsy so we don't clobber good prior
        # knowledge with nulls from a partially-failed run.
        patch = {k: v for k, v in patch.items() if v}
        if patch:
            platform_profiles.update(tenant_id, base_url,
                                     platform="puvi", patch=patch)
    except Exception:
        pass

    return PuviTestResult(ok=True, findings=findings, ground_truth=gt,
                          onboarding=onb, dashboard_url=dashboard_url)


def _score_accuracy(gt, findings) -> float:
    """Rough 0..1 score: 1.0 = Puvi reported everything accurately.

    We bias toward severity=confirmed since those are definitely wrong,
    not approximate. Used by the profile so future runs can flag sudden
    accuracy drops as a regression ('Puvi went from 0.98 last week to
    0.70 now — something broke').
    """
    if gt.total == 0:
        return 0.0
    penalties = 0.0
    for f in findings:
        if getattr(f, "source", "").startswith("puvi."):
            if f.severity == "confirmed":
                penalties += 0.2
            elif f.severity == "universal":
                penalties += 0.08
            elif f.severity == "configured":
                penalties += 0.04
    return max(0.0, 1.0 - penalties)
