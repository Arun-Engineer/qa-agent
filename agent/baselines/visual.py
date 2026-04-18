"""agent/baselines/visual.py — Screenshot-based visual baselines.

Captures a full-page screenshot via Playwright, hashes the bytes, stores the
hash as a `Baseline(kind="visual")`. A future run's capture is compared via
`oracles.confirmed.diff_against`.

Images themselves are written to `data/logs/baselines/<tenant>/<hash>.png`
so a human can eyeball what was approved — but the oracle comparison is
hash-based for speed. (Pixel-diff with tolerance can be layered on later
without changing this interface.)
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from agent.oracles.confirmed import Baseline, hash_artifact, approve


_BASELINE_DIR = Path(os.getenv("AUTO_BASELINE_DIR", "data/logs/baselines"))


def capture_visual_baseline(browser_context, url: str, *, scope: str = "",
                            tenant_id: str = "default",
                            approved_by: str = "",
                            auto_approve: bool = False) -> Optional[Baseline]:
    """Open `url`, screenshot it, optionally auto-approve as a baseline.

    `auto_approve=False` (default) is the correct production behavior — the
    UI surfaces candidates for a human to click "Approve". Auto-approve is
    for seeding the first run of a tenant where no human is available.
    """
    try:
        page = browser_context.new_page()
        page.goto(url, wait_until="networkidle", timeout=20000)
        img_dir = _BASELINE_DIR / tenant_id
        img_dir.mkdir(parents=True, exist_ok=True)
        tmp_path = img_dir / f"_tmp_{os.getpid()}.png"
        page.screenshot(path=str(tmp_path), full_page=True)
        page.close()
    except Exception:
        return None

    h = hash_artifact(tmp_path)
    final = img_dir / f"{h}.png"
    if not final.exists():
        tmp_path.replace(final)
    else:
        tmp_path.unlink(missing_ok=True)

    baseline = Baseline(
        id=f"{tenant_id}::visual::{scope or url}",
        kind="visual",
        scope=scope or url,
        hash=h,
        artifact_path=str(final),
        approved_by=approved_by or ("auto" if auto_approve else ""),
        tenant_id=tenant_id,
        meta={"original_url": url},
    )
    if auto_approve or approved_by:
        approve(baseline)
    return baseline
