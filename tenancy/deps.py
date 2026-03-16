# tenancy/deps.py
from __future__ import annotations

import os
from fastapi import Request, HTTPException, Depends
from sqlalchemy.orm import Session
from sqlalchemy import select

from auth.db import get_db
from auth.models import User  # legacy auth table (email + password_hash)
from tenancy.models import Tenant, Account, Membership

SESSION_USER = "user_id"
SESSION_ACCOUNT = "account_id"
SESSION_TENANT = "tenant_id"

DEFAULT_TENANT_SLUG = os.getenv("DEFAULT_TENANT", "local")
PLATFORM_GOD_EMAIL = os.getenv("PLATFORM_GOD_EMAIL", "").strip().lower()

# Dev-friendly default. For corporate, set ALLOW_AUTO_JOIN=0 and rely on invites.
ALLOW_AUTO_JOIN = os.getenv("ALLOW_AUTO_JOIN", "1") == "1"


def _host_only(request: Request) -> str:
    host = (request.headers.get("host") or "").strip().lower()
    if ":" in host and host.count(":") == 1:
        host = host.split(":")[0]
    return host


def require_tenant(request: Request, db: Session = Depends(get_db)) -> Tenant:
    """
    Normal tenant resolution:
      - uses request.state.tenant if middleware filled it
    Dev fallback:
      - if localhost/127.0.0.1 and no tenant, auto-create DEFAULT_TENANT
    """
    tenant = getattr(request.state, "tenant", None)
    if tenant:
        return tenant

    host = _host_only(request)

    # DEV fallback for localhost usage
    if host in ("localhost", "127.0.0.1"):
        t = db.execute(select(Tenant).where(Tenant.slug == DEFAULT_TENANT_SLUG)).scalar_one_or_none()
        if not t:
            t = Tenant(slug=DEFAULT_TENANT_SLUG, name=DEFAULT_TENANT_SLUG.upper(), is_active=True)
            db.add(t)
            db.commit()
            db.refresh(t)

        request.state.tenant = t
        request.state.tenant_id = t.id
        request.state.tenant_slug = t.slug
        return t

    raise HTTPException(status_code=400, detail="Tenant context missing")


def require_session(
    request: Request,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
):
    """
    Accepts:
      - tenant session: account_id + tenant_id
      - legacy session: user_id (auto-migrates to account+membership and sets session)

    SECURITY: Blocks pending/disabled memberships. Does NOT auto-create active
    memberships if a pending one already exists.
    """
    host_tid = getattr(request.state, "tenant_id", None) or tenant.id

    aid = request.session.get(SESSION_ACCOUNT)
    tid = request.session.get(SESSION_TENANT)

    # Already migrated tenant session — re-verify membership status
    if aid and tid and str(tid) == str(host_tid):
        mem = db.execute(
            select(Membership).where(
                Membership.account_id == aid,
                Membership.tenant_id == tid,
            )
        ).scalar_one_or_none()

        if mem and mem.status == "pending":
            request.session.clear()
            raise HTTPException(status_code=403, detail="Account awaiting admin approval")

        if mem and mem.status == "disabled":
            request.session.clear()
            raise HTTPException(status_code=403, detail="Account disabled in this org")

        if mem and mem.status == "active":
            return {"account_id": aid, "tenant_id": tid, "role": mem.role}

    # Legacy login session
    uid = request.session.get(SESSION_USER)
    if not uid:
        raise HTTPException(status_code=401, detail="Login required")

    user = db.get(User, uid)
    if not user:
        raise HTTPException(status_code=401, detail="Login required")

    email = (user.email or "").strip().lower()
    if not email:
        raise HTTPException(status_code=401, detail="Login required")

    # 1) ensure Account
    acct = db.execute(select(Account).where(Account.email == email)).scalar_one_or_none()
    if not acct:
        # copy password hash so later you can switch to Account auth if you want
        acct = Account(email=email, password_hash=user.password_hash, is_active=True)
        db.add(acct)
        db.commit()
        db.refresh(acct)

    # 2) Check for ANY existing membership (including pending/disabled)
    mem = db.execute(
        select(Membership).where(
            Membership.tenant_id == tenant.id,
            Membership.account_id == acct.id,
        )
    ).scalar_one_or_none()

    if mem:
        # SECURITY: Block pending users
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
        any_member = db.execute(
            select(Membership).where(Membership.tenant_id == tenant.id, Membership.status == "active")
        ).scalar_one_or_none()

        if not any_member:
            # No members at all — this is the first user, make them owner
            role = "owner"
            mem = Membership(tenant_id=tenant.id, account_id=acct.id, role=role, status="active")
            db.add(mem)
            db.commit()
            db.refresh(mem)
        else:
            # Tenant already has members — this user was removed or never approved
            # Do NOT auto-create. Block access.
            request.session.clear()
            raise HTTPException(
                status_code=403,
                detail="Access revoked. Please contact your admin or sign up again."
            )

    # 3) write back to session so future calls pass fast
    request.session[SESSION_ACCOUNT] = acct.id
    request.session[SESSION_TENANT] = tenant.id
    request.session["role"] = mem.role

    return {"account_id": acct.id, "tenant_id": tenant.id, "role": mem.role}

def get_session_user(
    request: Request,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
):
    """
    Returns a richer session-user object for RBAC + UI header.
    This matches what tenant_agent_api.py expects in /api/me and settings endpoints.
    """
    s = require_session(request=request, db=db, tenant=tenant)

    role = str(s.get("role") or request.session.get("role") or "viewer").lower()

    active_env = str(request.session.get("active_env") or os.getenv("DEFAULT_ENV", "UAT")).upper().strip()
    active_model = str(
        request.session.get("active_model")
        or os.getenv("DEFAULT_MODEL")
        or os.getenv("OPENAI_MODEL")
        or "gpt-4o-mini"
    ).strip()

    # Optional per-user overrides (keep empty if you're not using them yet)
    extra_envs = request.session.get("extra_envs") or []
    extra_perms = request.session.get("extra_perms") or []

    user = {
        "account_id": s["account_id"],
        "tenant_id": s["tenant_id"],
        "role": role,
        "active_env": active_env,
        "active_model": active_model,
        "extra_envs": extra_envs,
        "extra_perms": extra_perms,
    }

    # Handy for downstream middleware/handlers
    request.state.user = user
    return user