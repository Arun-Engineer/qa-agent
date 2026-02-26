# tenancy/routes_tenant.py
from fastapi import APIRouter, Request, Form, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import select

from auth.db import get_db
from tenancy.models import Account, Membership, Tenant
from auth.security import verify_password  # your password verify

router = APIRouter()
templates = Jinja2Templates(directory="templates")

SESSION_ACCOUNT = "account_id"
SESSION_TENANT = "tenant_id"


def require_tenant(request: Request):
    tenant = getattr(request.state, "tenant", None)
    if not tenant:
        # someone hit tenant route on apex
        raise RuntimeError("Tenant context missing")
    return tenant


def require_session(request: Request):
    """
    Protect tenant routes: must be logged in AND tenant must match host.
    """
    tid = request.session.get(SESSION_TENANT)
    aid = request.session.get(SESSION_ACCOUNT)
    host_tid = getattr(request.state, "tenant_id", None)
    if not aid or not tid or tid != host_tid:
        return None
    return {"account_id": aid, "tenant_id": tid}


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, tenant: Tenant = Depends(require_tenant), email: str | None = None):
    # prefill email from apex discovery if provided
    return templates.TemplateResponse(
        "login.html",
        {"request": request, "tenant": tenant, "prefill_email": email or "", "error_html": None},
    )


@router.post("/login")
def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
):
    email = (email or "").strip().lower()

    acct = db.execute(select(Account).where(Account.email == email, Account.is_active == True)).scalar_one_or_none()
    if not acct:
        return templates.TemplateResponse("login.html", {"request": request, "tenant": tenant, "prefill_email": email, "error_html": "Invalid credentials."}, status_code=401)

    if not verify_password(password, acct.password_hash):
        return templates.TemplateResponse("login.html", {"request": request, "tenant": tenant, "prefill_email": email, "error_html": "Invalid credentials."}, status_code=401)

    mem = db.execute(
        select(Membership).where(Membership.account_id == acct.id, Membership.tenant_id == tenant.id, Membership.status == "active")
    ).scalar_one_or_none()
    if not mem:
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "tenant": tenant, "prefill_email": email, "error_html": "You don’t have access to this org."},
            status_code=403,
        )

    request.session[SESSION_ACCOUNT] = acct.id
    request.session[SESSION_TENANT] = tenant.id
    request.session["role"] = mem.role

    return RedirectResponse(url="/dashboard", status_code=303)


@router.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request, tenant: Tenant = Depends(require_tenant), session=Depends(require_session)):
    if not session:
        return RedirectResponse(url="/login", status_code=303)

    return templates.TemplateResponse(
        "dashboard.html",
        {"request": request, "tenant": tenant, "role": request.session.get("role"), "account_id": session["account_id"]},
    )