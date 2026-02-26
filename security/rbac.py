from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Set


RBAC_PATH = Path("config/rbac.json")


@dataclass(frozen=True)
class RolePolicy:
    env_access: Set[str]
    permissions: Set[str]  # may contain "*"


@dataclass(frozen=True)
class RBACPolicy:
    roles: Dict[str, RolePolicy]
    all_permissions: Set[str]
    environments: Set[str]


@lru_cache(maxsize=1)
def load_rbac() -> RBACPolicy:
    raw = json.loads(RBAC_PATH.read_text(encoding="utf-8"))

    environments = set(raw.get("environments", []))
    all_permissions = set(raw.get("permissions", []))

    roles: Dict[str, RolePolicy] = {}
    for role_name, role_data in raw["roles"].items():
        env_access = set(role_data.get("env_access", []))
        permissions = set(role_data.get("permissions", []))
        roles[role_name] = RolePolicy(env_access=env_access, permissions=permissions)

    return RBACPolicy(roles=roles, all_permissions=all_permissions, environments=environments)


def role_has_permission(role: str, perm: str, extra_perms: Optional[Set[str]] = None) -> bool:
    policy = load_rbac()
    rp = policy.roles.get(role)
    if not rp:
        return False

    if "*" in rp.permissions:
        return True

    if extra_perms and perm in extra_perms:
        return True

    return perm in rp.permissions


def role_env_allowed(role: str, env: str, extra_envs: Optional[Set[str]] = None) -> bool:
    policy = load_rbac()
    if env not in policy.environments:
        return False

    rp = policy.roles.get(role)
    if not rp:
        return False

    if extra_envs and env in extra_envs:
        return True

    return env in rp.env_access