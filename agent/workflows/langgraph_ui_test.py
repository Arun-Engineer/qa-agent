"""agent/workflows/langgraph_ui_test.py — Self-healing UI test (Phase 5).

Per-action LangGraph:

    locate  →  act  →  verify
       ▲                  │
       └─── relocate ◀────┘   (on miss / stale element)

Locate:
  1. Try `selector_memory.recall(tenant, url_pattern, semantic)` first.
  2. Fall back to a heuristic locator (data-testid > aria-label > role+text > CSS).
  3. If still not found, capture a DOM snapshot + ask the LLM to propose a new
     selector based on the semantic description ("primary CTA button under hero").

Each successful locate writes back to selector_memory so the next run is
faster. Failed relocates decrement confidence; after MAX_HEALS we give up
and emit a `regression` finding.

Integration point: the autonomous executor calls `run_ui_action(...)` to
drive each UI step; this module encapsulates all the heal/memory logic.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import urlparse

from agent.memory import selector_memory


@dataclass
class UiActionResult:
    ok: bool
    semantic: str
    url: str
    selector_used: str = ""
    healed: bool = False
    heal_attempts: int = 0
    error: str = ""
    evidence: dict = field(default_factory=dict)


def _url_pattern(url: str) -> str:
    p = urlparse(url)
    # Group similar URLs — e.g. /product/123 and /product/456 share the pattern.
    parts = [seg for seg in p.path.split("/") if seg]
    normalized = ["{id}" if seg.isdigit() else seg for seg in parts]
    return f"{p.netloc}/{'/'.join(normalized)}" if p.netloc else "/" + "/".join(normalized)


def _heuristic_locate(page, semantic: str) -> Optional[str]:
    """Best-effort locator without LLM: data-testid → aria-label → role.
    Returns the selector that matched, or None."""
    sem = semantic.lower()
    candidates = [
        f'[data-testid="{sem}"]',
        f'[data-test-id="{sem}"]',
        f'[aria-label*="{sem}" i]',
        f'role=button[name*="{sem}" i]',
        f'text=/{sem}/i',
    ]
    for sel in candidates:
        try:
            if page.locator(sel).count() > 0:
                return sel
        except Exception:
            continue
    return None


def _llm_relocate(page, semantic: str) -> Optional[str]:
    """Ask the LLM to propose a stable selector for the semantic target.
    Keeps the DOM snapshot small (~6KB) to control token cost."""
    try:
        from src.agents.langgraph_runtime import llm_json
    except Exception:
        return None
    try:
        dom = page.evaluate("""
            () => {
              const clean = (el) => {
                if (!el) return '';
                const tag = el.tagName.toLowerCase();
                const id = el.id ? `#${el.id}` : '';
                const cls = (el.className && typeof el.className === 'string')
                    ? '.' + el.className.trim().split(/\\s+/).slice(0,2).join('.') : '';
                const txt = (el.innerText || '').slice(0, 40);
                return `<${tag}${id}${cls}>${txt}</${tag}>`;
              };
              return Array.from(document.querySelectorAll('button, a, input, [role]'))
                .slice(0, 60).map(clean).join('\\n');
            }
        """) or ""
    except Exception:
        dom = ""

    out = llm_json(
        messages=[
            {"role": "system",
             "content":
                 "You are a Playwright selector expert. Given a semantic target "
                 "and a DOM summary, propose ONE stable Playwright selector that "
                 "will locate the target. Prefer data-testid, aria-label, or "
                 "role+name over CSS classes. "
                 "Return JSON: {\"selector\": \"...\", \"rationale\": \"...\"}"},
            {"role": "user", "content": f"SEMANTIC: {semantic}\n\nDOM:\n{dom[:6000]}"},
        ],
        service="langgraph-ui-relocate",
        temperature=0.1,
    )
    sel = out.get("selector") if isinstance(out, dict) else None
    return sel if (sel and isinstance(sel, str)) else None


def run_ui_action(page, *, semantic: str, action: str,
                  action_args: Optional[dict] = None,
                  url: Optional[str] = None,
                  tenant_id: str = "default",
                  max_heals: int = 2) -> UiActionResult:
    """Execute a semantic UI action on `page` with memory + self-healing.

    `action` ∈ {"click", "fill", "check", "select"}. `action_args` carries
    extras like {"value": "..."} for fill/select.
    """
    url = url or getattr(page, "url", "")
    pattern = _url_pattern(url)
    args = action_args or {}
    attempts = 0
    last_error = ""

    # 1) Try remembered selector first.
    selector = selector_memory.recall(tenant_id, pattern, semantic)

    while attempts <= max_heals:
        if not selector:
            selector = _heuristic_locate(page, semantic)
        if not selector:
            selector = _llm_relocate(page, semantic)

        if selector:
            try:
                locator = page.locator(selector).first
                if action == "click":
                    locator.click(timeout=8000)
                elif action == "fill":
                    locator.fill(str(args.get("value", "")), timeout=8000)
                elif action == "check":
                    locator.check(timeout=8000)
                elif action == "select":
                    locator.select_option(args.get("value"))
                else:
                    raise ValueError(f"unknown action {action}")

                # Success — remember selector.
                selector_memory.remember(tenant_id, pattern, semantic,
                                         selector, succeeded=True)
                return UiActionResult(
                    ok=True, semantic=semantic, url=url,
                    selector_used=selector,
                    healed=(attempts > 0), heal_attempts=attempts,
                )
            except Exception as e:
                last_error = str(e)
                selector_memory.remember(tenant_id, pattern, semantic,
                                         selector, succeeded=False)

        attempts += 1
        selector = None  # force fresh relocate

    return UiActionResult(
        ok=False, semantic=semantic, url=url,
        selector_used=selector or "",
        healed=False, heal_attempts=attempts,
        error=last_error or "could not locate element after heal attempts",
    )
