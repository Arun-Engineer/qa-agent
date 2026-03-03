"""PROD Safety Guardrails — Prevents mutations during discovery in PROD.

Intercepts any write/mutation action when environment == PROD.
Used by the discovery engine to enforce read-only crawling in production.
"""
from __future__ import annotations

import structlog
from dataclasses import dataclass
from typing import Optional

logger = structlog.get_logger()

# Actions that are NEVER allowed in PROD
BLOCKED_ACTIONS_PROD = frozenset({
    "form_submit", "button_click_submit", "delete", "post_data",
    "create_account", "modify_state", "destructive_test",
    "generate_data", "write",
})

# Actions allowed in PROD (read-only observation)
ALLOWED_ACTIONS_PROD = frozenset({
    "navigate", "screenshot", "read_dom", "capture_network",
    "classify_page", "fingerprint_components", "observe",
})


@dataclass
class GuardrailResult:
    allowed: bool
    action: str
    environment: str
    reason: str


def check_action(action: str, environment: str, override: bool = False) -> GuardrailResult:
    """Check if an action is allowed in the given environment.

    Args:
        action: The action being attempted
        environment: The target environment (sit/uat/prod)
        override: If True, bypass checks (requires approval_required=False)

    Returns:
        GuardrailResult with allowed status and reason.
    """
    env = (environment or "sit").lower().strip()
    action_lower = action.lower().strip()

    # SIT: everything goes
    if env == "sit":
        return GuardrailResult(allowed=True, action=action, environment=env, reason="SIT: full access")

    # UAT: block destructive, allow rest
    if env == "uat":
        if action_lower in ("destructive_test", "delete", "generate_data"):
            return GuardrailResult(
                allowed=False, action=action, environment=env,
                reason=f"UAT: '{action}' blocked (destructive)"
            )
        return GuardrailResult(allowed=True, action=action, environment=env, reason="UAT: controlled access")

    # PROD: strict read-only
    if env == "prod":
        if override:
            logger.warning("prod_guardrail_override", action=action)
            return GuardrailResult(
                allowed=True, action=action, environment=env,
                reason="PROD: override granted (approval required)"
            )

        if action_lower in BLOCKED_ACTIONS_PROD:
            logger.warning("prod_action_blocked", action=action)
            return GuardrailResult(
                allowed=False, action=action, environment=env,
                reason=f"PROD: '{action}' blocked (read-only mode)"
            )

        if action_lower in ALLOWED_ACTIONS_PROD:
            return GuardrailResult(
                allowed=True, action=action, environment=env,
                reason="PROD: read-only action allowed"
            )

        # Unknown action in PROD → block by default (fail-safe)
        logger.warning("prod_unknown_action_blocked", action=action)
        return GuardrailResult(
            allowed=False, action=action, environment=env,
            reason=f"PROD: unknown action '{action}' blocked (fail-safe)"
        )

    # Unknown env → treat as PROD (fail-safe)
    return GuardrailResult(
        allowed=False, action=action, environment=env,
        reason=f"Unknown environment '{env}' — treating as PROD (fail-safe)"
    )
