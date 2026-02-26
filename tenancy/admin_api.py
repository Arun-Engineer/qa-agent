# tenancy/admin_api.py
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import os
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session
from sqlalchemy import select

from auth.db import get_db
from tenancy.models import Account, Membership, Invite, Tenant, PlatformRole
from tenancy.rbac import require_min_tenant_role, require_platform_role
from tenancy.audit import log_audit

router = APIRouter()

INVITE_SECRET = os.getenv("INVITE_SECRET", os.getenv("SESSION_SECRET", "dev-only-change-me"))
INVITE_TTL_HOURS = int(os.getenv("INVITE_TTL_HOURS", "72"))


def _hash_token(raw: str) -> str:
    return hmac.new(INVITE_SECRET.encode("utf-8"), raw.encode("utf-8"), hashlib.sha256).hexdigest()


class InviteCreate(BaseModel):
    email: EmailStr
    role: str = "member"  # owner/admin/member/viewer


class MemberUpdate(BaseModel):
    role: str | None = None
    status: str | None = None  # active|disabled


class TenantCreate(BaseModel):
    slug: str
    name: str


class GrantPlatformRole(BaseModel):
    email: EmailStr
    role: str  # none|support|billing|super_admin


@router.get("/api/admin/me")
def admin_me(
    request: Request,
    db: Session = Depends(get_db),
    ctx=Depends(require_min_tenant_role("viewer")),
):
    tenant: Tenant = ctx["tenant"]
    aid = ctx["account_id"]
    mem: Membership = ctx["membership"]

    acct = db.get(Account, aid)
    pr = db.execute(select(PlatformRole).where(PlatformRole.account_id == aid)).scalar_one_or_none()

    return {
        "tenant": {"id": tenant.id, "slug": tenant.slug, "name": tenant.name},
        "account": {"id": aid, "email": acct.email if acct else None},
        "tenant_role": mem.role,
        "platform_role": pr.role if pr else "none",
    }


@router.get("/api/admin/members")
def list_members(
    request: Request,
    db: Session = Depends(get_db),
    ctx=Depends(require_min_tenant_role("admin")),
):
    tenant: Tenant = ctx["tenant"]

    rows = db.execute(
        select(Membership, Account)
        .join(Account, Account.id == Membership.account_id)
        .where(Membership.tenant_id == tenant.id)
        .order_by(Membership.created_at.asc())
    ).all()

    out = []
    for mem, acct in rows:
        out.append(
            {
                "membership_id": mem.id,
                "account_id": acct.id,
                "email": acct.email,
                "role": mem.role,
                "status": mem.status,
                "created_at": mem.created_at.isoformat(),
            }
        )
    return out


@router.get("/api/admin/invites")
def list_invites(
    request: Request,
    db: Session = Depends(get_db),
    ctx=Depends(require_min_tenant_role("admin")),
):
    tenant: Tenant = ctx["tenant"]

    now = dt.datetime.utcnow()
    rows = db.execute(
        select(Invite).where(Invite.tenant_id == tenant.id).order_by(Invite.created_at.desc())
    ).scalars().all()

    out = []
    for inv in rows:
        out.append(
            {
                "invite_id": inv.id,
                "email": inv.email,
                "role": inv.role,
                "expires_at": inv.expires_at.isoformat(),
                "accepted_at": inv.accepted_at.isoformat() if inv.accepted_at else None,
                "is_expired": inv.expires_at < now,
                "created_at": inv.created_at.isoformat(),
            }
        )
    return out


@router.post("/api/admin/invite")
def create_invite(
    req: InviteCreate,
    request: Request,
    db: Session = Depends(get_db),
    ctx=Depends(require_min_tenant_role("admin")),
):
    tenant: Tenant = ctx["tenant"]
    actor = ctx["account_id"]

    role = req.role.strip().lower()
    if role not in ("owner", "admin", "member", "viewer"):
        raise HTTPException(400, "Invalid role")

    email = req.email.strip().lower()

    # if account already exists -> create membership directly
    acct = db.execute(select(Account).where(Account.email == email)).scalar_one_or_none()
    if acct:
        existing = db.execute(
            select(Membership).where(Membership.tenant_id == tenant.id, Membership.account_id == acct.id)
        ).scalar_one_or_none()
        if existing:
            raise HTTPException(400, "User already in tenant")

        mem = Membership(tenant_id=tenant.id, account_id=acct.id, role=role, status="active")
        db.add(mem)
        db.commit()

        log_audit(db, request, tenant.id, actor, "member_added", {"email": email, "role": role})
        return {"status": "added", "email": email, "role": role}

    # else create invite
    raw = secrets.token_urlsafe(32)
    token_hash = _hash_token(raw)
    expires = dt.datetime.utcnow() + dt.timedelta(hours=INVITE_TTL_HOURS)

    inv = Invite(tenant_id=tenant.id, email=email, role=role, token_hash=token_hash, expires_at=expires)
    db.add(inv)
    db.commit()
    db.refresh(inv)

    log_audit(db, request, tenant.id, actor, "invite_created", {"email": email, "role": role})

    # For now, we return the invite token for dev. In prod you would email it.
    invite_url = f"{str(request.base_url).rstrip('/')}/login?email={email}"
    return {"status": "invited", "invite_id": inv.id, "email": email, "role": role, "invite_url": invite_url, "dev_token": raw}


@router.patch("/api/admin/members/{membership_id}")
def update_member(
    membership_id: str,
    req: MemberUpdate,
    request: Request,
    db: Session = Depends(get_db),
    ctx=Depends(require_min_tenant_role("admin")),
):
    tenant: Tenant = ctx["tenant"]
    actor_id = ctx["account_id"]
    actor_mem: Membership = ctx["membership"]

    mem = db.execute(
        select(Membership).where(Membership.id == membership_id, Membership.tenant_id == tenant.id)
    ).scalar_one_or_none()
    if not mem:
        raise HTTPException(404, "Member not found")

    # Prevent admins from granting owner unless actor is owner
    if req.role:
        new_role = req.role.strip().lower()
        if new_role not in ("owner", "admin", "member", "viewer"):
            raise HTTPException(400, "Invalid role")

        if new_role == "owner" and actor_mem.role != "owner":
            raise HTTPException(403, "Only owner can grant owner role")

        # Optional: enforce single owner by demoting others
        if new_role == "owner":
            others = db.execute(
                select(Membership).where(Membership.tenant_id == tenant.id, Membership.role == "owner", Membership.id != mem.id)
            ).scalars().all()
            for o in others:
                o.role = "admin"

        mem.role = new_role

    if req.status:
        st = req.status.strip().lower()
        if st not in ("active", "disabled"):
            raise HTTPException(400, "Invalid status")

        # Don't let admin disable owner unless actor is owner
        if mem.role == "owner" and st == "disabled" and actor_mem.role != "owner":
            raise HTTPException(403, "Only owner can disable owner")

        # Don't allow disabling yourself (avoids lockouts)
        if mem.account_id == actor_id and st == "disabled":
            raise HTTPException(400, "You cannot disable yourself")

        mem.status = st

    db.commit()

    log_audit(
        db,
        request,
        tenant.id,
        actor_id,
        "member_updated",
        {"membership_id": membership_id, "role": mem.role, "status": mem.status},
    )

    return {"ok": True, "membership_id": mem.id, "role": mem.role, "status": mem.status}


@router.get("/api/admin/audit")
def audit_logs(
    request: Request,
    db: Session = Depends(get_db),
    ctx=Depends(require_min_tenant_role("admin")),
):
    tenant: Tenant = ctx["tenant"]

    from tenancy.models import AuditLog
    rows = db.execute(
        select(AuditLog)
        .where(AuditLog.tenant_id == tenant.id)
        .order_by(AuditLog.created_at.desc())
        .limit(100)
    ).scalars().all()

    out = []
    for r in rows:
        out.append(
            {
                "id": r.id,
                "action": r.action,
                "actor_account_id": r.actor_account_id,
                "ip": r.ip,
                "created_at": r.created_at.isoformat(),
                "meta": json.loads(r.meta_json or "{}"),
            }
        )
    return out


# ---------------------------
# Platform admin (super_admin)
# ---------------------------
@router.get("/api/platform/tenants")
def platform_list_tenants(
    request: Request,
    db: Session = Depends(get_db),
    ctx=Depends(require_platform_role("super_admin")),
):
    rows = db.execute(select(Tenant).order_by(Tenant.created_at.desc())).scalars().all()
    return [{"id": t.id, "slug": t.slug, "name": t.name, "is_active": t.is_active, "created_at": t.created_at.isoformat()} for t in rows]


@router.post("/api/platform/tenants")
def platform_create_tenant(
    req: TenantCreate,
    request: Request,
    db: Session = Depends(get_db),
    ctx=Depends(require_platform_role("super_admin")),
):
    slug = req.slug.strip().lower()
    if not slug or len(slug) > 63:
        raise HTTPException(400, "Invalid slug")

    exists = db.execute(select(Tenant).where(Tenant.slug == slug)).scalar_one_or_none()
    if exists:
        raise HTTPException(400, "Tenant already exists")

    t = Tenant(slug=slug, name=req.name.strip() or slug.upper(), is_active=True)
    db.add(t)
    db.commit()
    db.refresh(t)
    return {"ok": True, "tenant": {"id": t.id, "slug": t.slug, "name": t.name}}


@router.post("/api/platform/roles/grant")
def platform_grant_role(
    req: GrantPlatformRole,
    request: Request,
    db: Session = Depends(get_db),
    ctx=Depends(require_platform_role("super_admin")),
):
    email = req.email.strip().lower()
    role = req.role.strip().lower()
    if role not in ("none", "support", "billing", "super_admin"):
        raise HTTPException(400, "Invalid platform role")

    acct = db.execute(select(Account).where(Account.email == email)).scalar_one_or_none()
    if not acct:
        raise HTTPException(404, "Account not found")

    pr = db.execute(select(PlatformRole).where(PlatformRole.account_id == acct.id)).scalar_one_or_none()
    if not pr:
        pr = PlatformRole(account_id=acct.id, role=role)
        db.add(pr)
    else:
        pr.role = role

    db.commit()
    return {"ok": True, "email": email, "platform_role": role}