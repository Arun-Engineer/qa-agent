"""agent/workflows/replay.py — Re-run a past autonomous run.

Usage:
    new_ctx = replay_run(old_run_id, tenant_id="...")

Behavior:
  1. Load the old run's `ApplicationModel` snapshot from run_intel.
  2. Re-use the same execution profiles (you'll be prompted for creds again).
  3. Re-derive the suite from the snapshotted model — ensures we test the
     same surface the original run tested, not whatever the site looks like
     NOW (important for regression isolation).
  4. Execute.
  5. When done, diff old vs new via `agent.regression.diff_runs`.

Returns the new RunContext. The UI polls its status endpoint like any other
autonomous run.
"""
from __future__ import annotations

from typing import Any, Optional

from agent.discovery.app_model import ApplicationModel
from agent.memory import run_intel
from agent.workflows import autonomous_qa


def replay_run(old_run_id: str, *, tenant_id: str = "default") -> Optional[object]:
    snapshot = run_intel.load_model_snapshot(old_run_id)
    if not snapshot:
        return None
    try:
        model = ApplicationModel.from_dict(snapshot)
    except Exception:
        return None

    # Start a new autonomous run pointing at the base URL; tag its scope so
    # the driver skips crawl and uses the snapshotted model instead.
    ctx = autonomous_qa.start_run(
        model.base_url,
        scope={
            "replay_of": old_run_id,
            "tenant_id": tenant_id,
            "snapshot_model": snapshot,
        },
    )
    return ctx


def diff_with_origin(old_run_id: str, new_run: dict, *,
                     tenant_id: str = "default") -> dict[str, Any]:
    from agent import regression
    old_model = run_intel.load_model_snapshot(old_run_id) or {}
    old_run = {"model": old_model, "findings": []}  # TODO: rehydrate findings
    return regression.diff_runs(old_run, new_run, tenant_id=tenant_id)
