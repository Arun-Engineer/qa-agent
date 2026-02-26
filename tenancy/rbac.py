# tenancy/rbac.py
from __future__ import annotations

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session
from sqlalchemy import select

from auth.db import get_db
from tenancy.models import Account, Membership, PlatformRole, Tenant


TENANT_ROLE_LEVEL = {"viewer": 1, "member": 2, "admin": 3, "owner": 4}
PLATFORM_ROLE_LEVEL = {"none": 0, "support": 1, "billing": 2, "super_admin": 3}


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
    """
    # already tenant-mode session
    if request.session.get("account_id") and request.session.get("tenant_id"):
        return {
            "account_id": str(request.session["account_id"]),
            "tenant_id": str(request.session["tenant_id"]),
            "role": request.session.get("role") or "member",
        }

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

    mem = db.execute(
        select(Membership).where(
            Membership.account_id == acct.id,
            Membership.tenant_id == tenant.id,
        )
    ).scalar_one_or_none()

    if not mem:
        # first membership in tenant -> owner, otherwise member
        existing = db.execute(select(Membership).where(Membership.tenant_id == tenant.id)).scalar_one_or_none()
        role = "owner" if not existing else "member"
        mem = Membership(tenant_id=tenant.id, account_id=acct.id, role=role, status="active")
        db.add(mem)
        db.commit()
        db.refresh(mem)

    if mem.status != "active":
        raise HTTPException(status_code=403, detail="Account disabled in this org")

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