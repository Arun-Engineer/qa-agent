# tenancy/admin_users_api.py
"""
Admin User Management API
- GET  /api/admin/users       → List all registered users with role, status, signup date
- DELETE /api/admin/users/{id} → Remove a user (membership + optionally account)
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from sqlalchemy import select, func

from auth.db import get_db
from tenancy.models import Account, Membership, Tenant
from tenancy.rbac import require_min_tenant_role
from tenancy.audit import log_audit

router = APIRouter()


@router.get("/api/admin/users")
def list_all_users(
    request: Request,
    db: Session = Depends(get_db),
    ctx=Depends(require_min_tenant_role("admin")),
):
    """
    Returns all users registered in this tenant with:
    - email, role, status, registered date, account_id, membership_id
    Sorted by registration date (newest first).
    """
    tenant: Tenant = ctx["tenant"]

    rows = db.execute(
        select(Membership, Account)
        .join(Account, Account.id == Membership.account_id)
        .where(Membership.tenant_id == tenant.id)
        .order_by(Membership.created_at.desc())
    ).all()

    users = []
    for mem, acct in rows:
        users.append({
            "membership_id": mem.id,
            "account_id": acct.id,
            "email": acct.email,
            "role": mem.role,
            "status": mem.status,
            "registered_at": mem.created_at.isoformat(),
            "is_active": acct.is_active,
        })

    # Summary counts
    total = len(users)
    by_role = {}
    by_status = {}
    for u in users:
        by_role[u["role"]] = by_role.get(u["role"], 0) + 1
        by_status[u["status"]] = by_status.get(u["status"], 0) + 1

    return {
        "users": users,
        "summary": {
            "total": total,
            "by_role": by_role,
            "by_status": by_status,
        },
    }


@router.delete("/api/admin/users/{membership_id}")
def remove_user(
    membership_id: str,
    request: Request,
    db: Session = Depends(get_db),
    ctx=Depends(require_min_tenant_role("admin")),
):
    """
    Remove a user from the tenant.
    - Deletes their Membership (removes access)
    - Does NOT delete the Account (they can be re-invited)
    - Admins cannot remove owners unless they are owner themselves
    - Cannot remove yourself
    """
    tenant: Tenant = ctx["tenant"]
    actor_id = ctx["account_id"]
    actor_mem: Membership = ctx["membership"]

    mem = db.execute(
        select(Membership).where(
            Membership.id == membership_id,
            Membership.tenant_id == tenant.id,
        )
    ).scalar_one_or_none()

    if not mem:
        raise HTTPException(404, "User not found in this tenant")

    # Cannot remove yourself
    if str(mem.account_id) == str(actor_id):
        raise HTTPException(400, "You cannot remove yourself")

    # Only owner can remove another owner
    if mem.role == "owner" and actor_mem.role != "owner":
        raise HTTPException(403, "Only owner can remove another owner")

    # Get email for audit log before deleting
    acct = db.get(Account, mem.account_id)
    email = acct.email if acct else "unknown"

    db.delete(mem)

    log_audit(db, request, tenant.id, actor_id, "member.removed", {
        "membership_id": membership_id,
        "email": email,
        "role": mem.role,
    })

    db.commit()

    return {"ok": True, "removed": email, "membership_id": membership_id}


from pydantic import BaseModel, EmailStr


class UserActionByEmail(BaseModel):
    email: EmailStr
    action: str       # "remove" | "change_role" | "disable" | "enable"
    role: str = ""    # required when action == "change_role"


@router.post("/api/admin/users/manage")
def manage_user_by_email(
    req: UserActionByEmail,
    request: Request,
    db: Session = Depends(get_db),
    ctx=Depends(require_min_tenant_role("admin")),
):
    """
    Admin quick-action: manage a user by email.
    Actions: remove, change_role, disable, enable
    """
    tenant: Tenant = ctx["tenant"]
    actor_id = ctx["account_id"]
    actor_mem: Membership = ctx["membership"]

    email = req.email.strip().lower()
    action = req.action.strip().lower()

    # Find account by email
    acct = db.execute(select(Account).where(Account.email == email)).scalar_one_or_none()
    if not acct:
        raise HTTPException(404, f"No account found for {email}")

    # Find membership in this tenant
    mem = db.execute(
        select(Membership).where(
            Membership.account_id == acct.id,
            Membership.tenant_id == tenant.id,
        )
    ).scalar_one_or_none()

    if not mem:
        raise HTTPException(404, f"{email} is not a member of this tenant")

    # Cannot act on yourself
    if str(acct.id) == str(actor_id):
        raise HTTPException(400, "You cannot modify your own account here")

    # Only owner can modify another owner
    if mem.role == "owner" and actor_mem.role != "owner":
        raise HTTPException(403, "Only owner can modify another owner")

    if action == "remove":
        old_role = mem.role
        db.delete(mem)
        log_audit(db, request, tenant.id, actor_id, "member.removed", {
            "email": email, "role": old_role,
        })
        db.commit()
        return {"ok": True, "action": "removed", "email": email}

    elif action == "change_role":
        new_role = req.role.strip().lower()
        if new_role not in ("owner", "admin", "member", "viewer"):
            raise HTTPException(400, "Invalid role. Use: owner, admin, member, viewer")
        if new_role == "owner" and actor_mem.role != "owner":
            raise HTTPException(403, "Only owner can grant owner role")

        old_role = mem.role
        mem.role = new_role
        log_audit(db, request, tenant.id, actor_id, "member.role_changed", {
            "email": email, "old_role": old_role, "new_role": new_role,
        })
        db.commit()
        return {"ok": True, "action": "role_changed", "email": email, "old_role": old_role, "new_role": new_role}

    elif action == "disable":
        mem.status = "disabled"
        log_audit(db, request, tenant.id, actor_id, "member.disabled", {"email": email})
        db.commit()
        return {"ok": True, "action": "disabled", "email": email}

    elif action == "enable":
        mem.status = "active"
        log_audit(db, request, tenant.id, actor_id, "member.enabled", {"email": email})
        db.commit()
        return {"ok": True, "action": "enabled", "email": email}

    else:
        raise HTTPException(400, "Invalid action. Use: remove, change_role, disable, enable")
