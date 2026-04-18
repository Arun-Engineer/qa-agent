"""agent/profiles/execution_profile.py — Bundle identity + environment + data.

The autonomous executor needs to know, for each test step:
  * Which role to run it under (anonymous, customer, admin, …)
  * Which environment (UAT, STAGING, PROD)
  * Which auth plugin + credential reference to use
  * Which tenant's data/feature-flags apply

Rather than scatter those across function arguments, a `ExecutionProfile`
bundles them. The executor selects the right profile per step from the
available set, based on the step's declared role requirement.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class ExecutionProfile:
    """One identity's worth of run configuration."""
    name: str                              # profile id, e.g. "customer@UAT"
    role: str                              # "anonymous" | "customer" | "admin" | …
    tenant_id: str = "default"
    env: str = "UAT"
    auth_plugin: str = ""                  # "" means no auth needed
    cred_ref: str = ""                     # opaque handle into cred_vault
    data: dict[str, Any] = field(default_factory=dict)
    feature_flags: dict[str, bool] = field(default_factory=dict)

    def requires_auth(self) -> bool:
        return self.role != "anonymous" and bool(self.auth_plugin)


def profile_for_role(role_name: str, *, tenant_id: str = "default",
                     env: str = "UAT", auth_plugin: str = "form_login") -> ExecutionProfile:
    """Factory: a sensible default profile for a given role name."""
    if role_name == "anonymous":
        return ExecutionProfile(
            name=f"anonymous@{env}",
            role="anonymous",
            tenant_id=tenant_id,
            env=env,
        )
    return ExecutionProfile(
        name=f"{role_name}@{env}",
        role=role_name,
        tenant_id=tenant_id,
        env=env,
        auth_plugin=auth_plugin,
        cred_ref=role_name,      # role_name doubles as the vault key for phase 1
    )


def pick_profile(step: dict, profiles: list[ExecutionProfile]) -> Optional[ExecutionProfile]:
    """Given a step with an optional `role` field, return the matching profile
    or None if no match + no anonymous fallback."""
    role = step.get("role") or "anonymous"
    for p in profiles:
        if p.role == role:
            return p
    # Fall back to anonymous profile if present.
    return next((p for p in profiles if p.role == "anonymous"), None)
