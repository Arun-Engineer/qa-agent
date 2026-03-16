import os
# tenancy/rbac.py
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session
from sqlalchemy import select

from auth.db import get_db
from tenancy.models import Account, Membership, PlatformRole, Tenant


PLATFORM_GOD_EMAIL = os.getenv("PLATFORM_GOD_EMAIL", "").strip().lower()

TENANT_ROLE_LEVEL = {"viewer": 1, "member": 2, "admin": 3, "owner": 4}
PLATFORM_ROLE_LEVEL = {"none": 0, "support": 1, "billing": 2, "super_admin": 3}
RBAC_PATH = Path("config/rbac.json")


@dataclass(frozen=True)
class RolePolicy:
    env_access: set[str]
    permissions: set[str]


@dataclass(frozen=True)
class RBACPolicy:
    roles: dict[str, RolePolicy]
    all_permissions: set[str]
    environments: set[str]


@lru_cache(maxsize=1)
def load_rbac_policy() -> RBACPolicy:
    raw = json.loads(RBAC_PATH.read_text(encoding="utf-8"))
    environments = {str(e).upper() for e in raw.get("environments", [])}
    all_permissions = {str(p) for p in raw.get("permissions", [])}

    roles: dict[str, RolePolicy] = {}
    for role_name, role_data in raw.get("roles", {}).items():
        roles[str(role_name).lower()] = RolePolicy(
            env_access={str(e).upper() for e in role_data.get("env_access", [])},
            permissions={str(p) for p in role_data.get("permissions", [])},
        )

    return RBACPolicy(roles=roles, all_permissions=all_permissions, environments=environments)


def role_has_permission(role: str, permission: str, extra_perms: set[str] | None = None) -> bool:
    policy = load_rbac_policy()
    rp = policy.roles.get((role or "").lower())
    if not rp:
        return False

    if "*" in rp.permissions:
        return True

    if extra_perms and permission in extra_perms:
        return True

    return permission in rp.permissions


def role_env_allowed(role: str, env: str, extra_envs: set[str] | None = None) -> bool:
    policy = load_rbac_policy()
    env_norm = (env or "").upper()
    if env_norm not in policy.environments:
        return False

    rp = policy.roles.get((role or "").lower())
    if not rp:
        return False

    if extra_envs and env_norm in extra_envs:
        return True

    return env_norm in rp.env_access


def available_envs_for_role(role: str, extra_envs: set[str] | None = None) -> list[str]:
    policy = load_rbac_policy()
    rp = policy.roles.get((role or "").lower())
    base_envs = set(rp.env_access) if rp else set()
    if extra_envs:
        base_envs.update({e.upper() for e in extra_envs})
    return [e for e in sorted(base_envs) if e in policy.environments]


def effective_permissions_for_role(role: str, extra_perms: set[str] | None = None) -> list[str]:
    """
    Returns the effective permissions for a role, optionally merged with per-user overrides.
    If role has wildcard (*), return all configured permissions plus extra_perms.
    """
    policy = load_rbac_policy()
    rp = policy.roles.get((role or "").lower())
    if not rp:
        return sorted(list(extra_perms or set()))

    if "*" in rp.permissions:
        perms = set(policy.all_permissions)
    else:
        perms = set(rp.permissions)

    if extra_perms:
        perms.update(extra_perms)

    return sorted(list(perms))


def _require_account_id(request: Request) -> str:
    # Accept either tenant session or legacy auth user session
    aid = request.session.get("account_id")
    if aid:
        return str(aid)

    # legacy "user_id" flow: try to map auth User -> Account on the fly
    uid = request.session.get("user_id")
    if not uid:
        raise HTTPException(status_code=401, detail="Login required")

    try:
        from auth.models import User  # type: ignore
    except Exception:
        raise HTTPException(status_code=401, detail="Login required")

    # We need DB here; handled by wrapper deps below.
    raise RuntimeError("account sync requires db")


def ensure_account_session(
    request: Request,
    db: Session,
    tenant: Tenant,
) -> dict:
    """
    If user logged in via legacy auth (user_id), auto-create/lookup Account + Membership
    and set session keys: account_id, tenant_id, role.

    SECURITY: If user has a "pending" membership, raise 403 — do NOT auto-create active.
    """
    # already tenant-mode session — but verify the membership is still active
    if request.session.get("account_id") and request.session.get("tenant_id"):
        aid = str(request.session["account_id"])
        tid = str(request.session["tenant_id"])

        # ── SECURITY: Re-verify membership status on every request ──
        mem = db.execute(
            select(Membership).where(
                Membership.account_id == aid,
                Membership.tenant_id == tid,
            )
        ).scalar_one_or_none()

        if mem and mem.status == "pending":
            # Clear the session — user shouldn't be logged in
            request.session.clear()
            raise HTTPException(status_code=403, detail="Account awaiting admin approval")

        if mem and mem.status == "disabled":
            request.session.clear()
            raise HTTPException(status_code=403, detail="Account disabled in this org")

        if mem and mem.status == "active":
            return {
                "account_id": aid,
                "tenant_id": tid,
                "role": mem.role or "member",
            }

        # If no membership found but session exists, fall through to re-create

    uid = request.session.get("user_id")
    if not uid:
        raise HTTPException(status_code=401, detail="Login required")

    try:
        from auth.models import User  # type: ignore
    except Exception:
        raise HTTPException(status_code=401, detail="Login required")

    user = db.get(User, uid)
    if not user:
        raise HTTPException(status_code=401, detail="Login required")

    email = (getattr(user, "email", "") or "").strip().lower()
    pw_hash = getattr(user, "password_hash", None) or getattr(user, "password", None)

    if not email:
        raise HTTPException(status_code=401, detail="Login required")

    acct = db.execute(select(Account).where(Account.email == email)).scalar_one_or_none()
    if not acct:
        # create an Account mapped to this auth User
        acct = Account(email=email, password_hash=pw_hash or "migrated", is_active=True)
        db.add(acct)
        db.commit()
        db.refresh(acct)

    # ── Check for ANY existing membership (including pending) ──
    mem = db.execute(
        select(Membership).where(
            Membership.account_id == acct.id,
            Membership.tenant_id == tenant.id,
        )
    ).scalar_one_or_none()

    if mem:
        # ── SECURITY: Block pending users ──
        if mem.status == "pending":
            request.session.clear()
            raise HTTPException(status_code=403, detail="Account awaiting admin approval")

        if mem.status == "disabled":
            request.session.clear()
            raise HTTPException(status_code=403, detail="Account disabled in this org")

        if mem.status != "active":
            request.session.clear()
            raise HTTPException(status_code=403, detail="Account not active")
    else:
        # No membership at all — user was removed or never approved
        # Only auto-create for the very FIRST user (owner bootstrap)
        existing = db.execute(
            select(Membership).where(Membership.tenant_id == tenant.id)
        ).scalar_one_or_none()

        if not existing:
            # No members at all — this is the first user, make them owner
            role = "owner"
            mem = Membership(tenant_id=tenant.id, account_id=acct.id, role=role, status="active")
            db.add(mem)
            db.commit()
            db.refresh(mem)
        else:
            # Tenant already has members — this user was removed or never approved
            request.session.clear()
            raise HTTPException(
                status_code=403,
                detail="Access revoked. Please contact your admin or sign up again."
            )

    request.session["account_id"] = acct.id
    request.session["tenant_id"] = tenant.id
    request.session["role"] = mem.role

    return {"account_id": acct.id, "tenant_id": tenant.id, "role": mem.role}


def require_session_ctx(
    request: Request,
    db: Session = Depends(get_db),
):
    tenant: Tenant = getattr(request.state, "tenant", None)
    if not tenant:
        raise HTTPException(status_code=400, detail="Tenant context missing")
    return ensure_account_session(request, db, tenant)


def require_min_tenant_role(min_role: str):
    min_level = TENANT_ROLE_LEVEL.get(min_role, 999)

    def _dep(
        request: Request,
        db: Session = Depends(get_db),
    ):
        tenant: Tenant = getattr(request.state, "tenant", None)
        if not tenant:
            raise HTTPException(status_code=400, detail="Tenant context missing")

        ctx = ensure_account_session(request, db, tenant)
        aid = ctx["account_id"]

        mem = db.execute(
            select(Membership).where(
                Membership.tenant_id == tenant.id,
                Membership.account_id == aid,
                Membership.status == "active",
            )
        ).scalar_one_or_none()

        if not mem:
            raise HTTPException(status_code=403, detail="No access to this org")

        lvl = TENANT_ROLE_LEVEL.get(mem.role or "viewer", 0)
        if lvl < min_level:
            raise HTTPException(status_code=403, detail="Insufficient role")

        # refresh session role to avoid stale role
        request.session["role"] = mem.role
        return {"tenant": tenant, "account_id": aid, "membership": mem}

    return _dep


def require_platform_role(min_role: str):
    min_level = PLATFORM_ROLE_LEVEL.get(min_role, 999)

    def _dep(
        request: Request,
        db: Session = Depends(get_db),
    ):
        tenant: Tenant = getattr(request.state, "tenant", None)
        if not tenant:
            raise HTTPException(status_code=400, detail="Tenant context missing")

        ctx = ensure_account_session(request, db, tenant)
        aid = ctx["account_id"]

        pr = db.execute(select(PlatformRole).where(PlatformRole.account_id == aid)).scalar_one_or_none()
        role = pr.role if pr else "none"
        if PLATFORM_ROLE_LEVEL.get(role, 0) < min_level:
            raise HTTPException(status_code=403, detail="Platform access denied")

        return {"tenant": tenant, "account_id": aid, "platform_role": role}

    return _dep