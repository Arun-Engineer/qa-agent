# tenancy/routes_apex.py
import os
from fastapi import APIRouter, Request, Form, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import select

from auth.db import get_db
from tenancy.models import Account, Membership, Tenant, Invite

router = APIRouter()
templates = Jinja2Templates(directory="templates")

BASE_DOMAIN = os.getenv("BASE_DOMAIN", "localhost")
SCHEME = os.getenv("PUBLIC_SCHEME", "http")  # in prod: https


def tenant_url(slug: str, path: str = "/login") -> str:
    path = path if path.startswith("/") else f"/{path}"
    return f"{SCHEME}://{slug}.{BASE_DOMAIN}:8000{path}" if BASE_DOMAIN == "localhost" else f"{SCHEME}://{slug}.{BASE_DOMAIN}{path}"


@router.get("/", response_class=HTMLResponse)
def apex_home(request: Request):
    # If someone hits apex, force discovery page
    return templates.TemplateResponse("discover.html", {"request": request, "error": None})


@router.post("/discover", response_class=HTMLResponse)
def discover_submit(
    request: Request,
    email: str = Form(...),
    db: Session = Depends(get_db),
):
    email = (email or "").strip().lower()
    if "@" not in email:
        return templates.TemplateResponse("discover.html", {"request": request, "error": "Enter a valid email."}, status_code=400)

    # 1) existing account memberships
    acct = db.execute(select(Account).where(Account.email == email)).scalar_one_or_none()
    memberships = []
    if acct:
        memberships = db.execute(
            select(Membership, Tenant)
            .join(Tenant, Tenant.id == Membership.tenant_id)
            .where(Membership.account_id == acct.id, Membership.status == "active", Tenant.is_active == True)
        ).all()

    # 2) if no account, check invites (invite-only corporate flow)
    invites = []
    if not memberships:
        invites = db.execute(
            select(Invite, Tenant)
            .join(Tenant, Tenant.id == Invite.tenant_id)
            .where(Invite.email == email, Invite.accepted_at.is_(None), Tenant.is_active == True)
        ).all()

    # Single tenant -> redirect straight there
    if len(memberships) == 1:
        _m, t = memberships[0]
        return RedirectResponse(url=tenant_url(t.slug, f"/login?email={email}"), status_code=303)

    if len(invites) == 1 and not memberships:
        _inv, t = invites[0]
        return RedirectResponse(url=tenant_url(t.slug, f"/signup?email={email}"), status_code=303)

    # Multiple tenants -> org picker
    if len(memberships) > 1:
        orgs = [{"name": t.name, "slug": t.slug, "url": tenant_url(t.slug, f"/login?email={email}")} for _m, t in memberships]
        return templates.TemplateResponse("org_picker.html", {"request": request, "email": email, "orgs": orgs})

    if len(invites) > 1 and not memberships:
        orgs = [{"name": t.name, "slug": t.slug, "url": tenant_url(t.slug, f"/signup?email={email}")} for _i, t in invites]
        return templates.TemplateResponse("org_picker.html", {"request": request, "email": email, "orgs": orgs})

    # No org found
    return templates.TemplateResponse(
        "discover.html",
        {"request": request, "error": "No organization found for this email. Ask your admin to invite you."},
        status_code=404
    )