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

    # ✅ DEV fallback for localhost usage
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
    """
    host_tid = getattr(request.state, "tenant_id", None) or tenant.id

    aid = request.session.get(SESSION_ACCOUNT)
    tid = request.session.get(SESSION_TENANT)

    # ✅ Already migrated tenant session
    if aid and tid and str(tid) == str(host_tid):
        return {"account_id": aid, "tenant_id": tid, "role": request.session.get("role")}

    # ✅ Legacy login session
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

    # 2) ensure Membership
    mem = db.execute(
        select(Membership).where(
            Membership.tenant_id == tenant.id,
            Membership.account_id == acct.id,
            Membership.status == "active",
        )
    ).scalar_one_or_none()

    if not mem:
        if not ALLOW_AUTO_JOIN:
            raise HTTPException(status_code=403, detail="No access to this org (invite required).")

        # first member becomes owner (dev-friendly)
        any_member = db.execute(
            select(Membership).where(Membership.tenant_id == tenant.id, Membership.status == "active")
        ).scalar_one_or_none()

        role = "owner" if not any_member else "member"
        mem = Membership(tenant_id=tenant.id, account_id=acct.id, role=role, status="active")
        db.add(mem)
        db.commit()
        db.refresh(mem)

    # 3) write back to session so future calls pass fast
    request.session[SESSION_ACCOUNT] = acct.id
    request.session[SESSION_TENANT] = tenant.id
    request.session["role"] = mem.role

    return {"account_id": acct.id, "tenant_id": tenant.id, "role": mem.role}