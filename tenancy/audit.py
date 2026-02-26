# tenancy/audit.py
from __future__ import annotations

import json
from sqlalchemy.orm import Session
from fastapi import Request

from tenancy.models import AuditLog


def log_audit(db: Session, request: Request, tenant_id: str, actor_account_id: str | None, action: str, meta: dict | None = None):
    ip = None
    try:
        ip = request.client.host if request.client else None
    except Exception:
        pass

    ua = request.headers.get("user-agent")
    row = AuditLog(
        tenant_id=str(tenant_id),
        actor_account_id=str(actor_account_id) if actor_account_id else None,
        action=action,
        ip=ip,
        user_agent=ua,
        meta_json=json.dumps(meta or {}, ensure_ascii=False),
    )
    db.add(row)
    db.commit()