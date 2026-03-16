import os
import secrets
import datetime as dt
import hashlib
import hmac

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import select

from .db import get_db
from .models import User, PasswordResetToken
from .security import hash_password, verify_password

# passlib backend errors (nice UI instead of 500)
try:
    from passlib.exc import MissingBackendError
except Exception:  # pragma: no cover
    MissingBackendError = Exception  # fallback

router = APIRouter()
templates = Jinja2Templates(directory="templates")

SESSION_KEY = "user_id"
SESSION_TENANT = "tenant_id"  # keep tenant_id for tenant-aware API routes (even in localhost mode)

RESET_SECRET = os.getenv("RESET_SECRET", os.getenv("SESSION_SECRET", "dev-only-change-me"))
RESET_TTL_MINUTES = int(os.getenv("RESET_TOKEN_TTL_MINUTES", "15"))
SHOW_RESET_LINK = os.getenv("SHOW_RESET_LINK", "1") == "1"  # turn OFF in prod

# bcrypt limit: 72 BYTES (not chars)
MAX_BCRYPT_BYTES = int(os.getenv("MAX_BCRYPT_BYTES", "72"))

# Owner email that bypasses pending check (first user / admin)
OWNER_EMAIL = os.getenv("OWNER_EMAIL", "").strip().lower()
PLATFORM_GOD_EMAIL = os.getenv("PLATFORM_GOD_EMAIL", "").strip().lower()
ADMIN_BOOTSTRAP_KEY = os.getenv("ADMIN_BOOTSTRAP_KEY", "").strip()


def _hash_token(raw_token: str) -> str:
    # HMAC-SHA256 so stored value is useless if DB leaks
    return hmac.new(
        RESET_SECRET.encode("utf-8"),
        raw_token.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()


def get_current_user(request: Request, db: Session) -> User | None:
    uid = request.session.get(SESSION_KEY)
    if not uid:
        return None
    return db.get(User, uid)


def _ensure_tenant_session(request: Request):
    """
    If asgi middleware sets request.state.tenant_id, persist it into session.
    This avoids "logged in but API says Login required" in tenant-aware deps.
    """
    if request.session.get(SESSION_TENANT):
        return
    host_tid = getattr(request.state, "tenant_id", None) or getattr(request.state, "tenant_slug", None)
    if host_tid:
        request.session[SESSION_TENANT] = host_tid


def _password_error(pw: str) -> str | None:
    """
    Returns an error string if invalid, else None.
    Enforces bcrypt 72-byte limit to avoid 500s.
    """
    if not isinstance(pw, str) or not pw:
        return "Password is required."
    if len(pw) < 8:
        return "Password must be at least 8 characters."
    # bytes length matters for bcrypt
    if len(pw.encode("utf-8")) > MAX_BCRYPT_BYTES:
        return f"Password too long. Max {MAX_BCRYPT_BYTES} bytes (bcrypt limit). Use a shorter password."
    # optional sanity cap (prevents absurd inputs)
    if len(pw) > 256:
        return "Password is too long."
    return None


def _check_pending_membership(db: Session, email: str) -> bool:
    """
    Check if this user should be BLOCKED from login.
    Returns True to BLOCK if: pending membership, or no membership at all (removed/never approved).
    Owner email always bypasses this check.
    """
    email_lower = email.strip().lower()
    if OWNER_EMAIL and email_lower == OWNER_EMAIL:
        return False  # owner never blocked
    if PLATFORM_GOD_EMAIL and email_lower == PLATFORM_GOD_EMAIL:
        return False  # platform god never blocked

    try:
        from tenancy.models import Account, Membership
        acct = db.execute(select(Account).where(Account.email == email)).scalar_one_or_none()
        if not acct:
            return False  # no account yet, login will fail anyway on password check

        # Check if ANY membership for this account is pending
        pending = db.execute(
            select(Membership).where(
                Membership.account_id == acct.id,
                Membership.status == "pending",
            )
        ).scalar_one_or_none()

        if pending:
            return True  # BLOCK — user is pending approval

        # Check: does user have ANY active membership?
        active = db.execute(
            select(Membership).where(
                Membership.account_id == acct.id,
                Membership.status == "active",
            )
        ).scalar_one_or_none()

        if not active:
            # No active membership AND no pending = user was removed or never approved
            # BLOCK them — they must sign up again
            return True

    except Exception:
        # If tenancy models aren't available (early dev), don't block
        pass

    return False


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, db: Session = Depends(get_db)):
    # If already logged in, go straight to dashboard
    user = get_current_user(request, db)
    if user:
        _ensure_tenant_session(request)
        return RedirectResponse(url="/dashboard", status_code=303)

    return templates.TemplateResponse("login.html", {"request": request, "error_html": None})


@router.post("/login", response_class=HTMLResponse)
def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    email = (email or "").strip().lower()

    if "@" not in email or len(email) > 320:
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error_html": "Enter a valid email."},
            status_code=400,
        )

    # avoid bcrypt crash on huge password inputs (yes, it can crash verify too)
    pw_err = _password_error(password)
    if pw_err:
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error_html": pw_err},
            status_code=400,
        )

    user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if not user:
        return templates.TemplateResponse(
            "login.html",
            {
                "request": request,
                "error_html": 'No account found for this email. <a href="/signup">Create an account</a>.',
            },
            status_code=400,
        )

    try:
        ok = verify_password(password, user.password_hash)
    except MissingBackendError:
        return templates.TemplateResponse(
            "login.html",
            {
                "request": request,
                "error_html": "Password backend missing. Install bcrypt: <code>pip install bcrypt</code>",
            },
            status_code=500,
        )
    except Exception:
        return templates.TemplateResponse(
            "login.html",
            {
                "request": request,
                "error_html": "Login failed due to a server password backend issue.",
            },
            status_code=500,
        )

    if not ok:
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error_html": "Wrong password. Please try again."},
            status_code=400,
        )

    # ── SECURITY: Block pending/removed users from logging in ──
    if _check_pending_membership(db, email):
        # Determine whether pending or removed for accurate message
        try:
            from tenancy.models import Account, Membership
            acct = db.execute(select(Account).where(Account.email == email)).scalar_one_or_none()
            if acct:
                has_pending = db.execute(
                    select(Membership).where(
                        Membership.account_id == acct.id,
                        Membership.status == "pending",
                    )
                ).scalar_one_or_none()
                if has_pending:
                    msg = "Your account is awaiting admin approval. You will be notified once approved."
                else:
                    msg = ('Your access has been revoked. Please contact your admin or '
                           '<a href="/signup">create a new account</a>.')
            else:
                msg = 'No account found. <a href="/signup">Create an account</a>.'
        except Exception:
            msg = "Access denied. Please contact your admin."

        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error_html": msg},
            status_code=403,
        )

    request.session[SESSION_KEY] = user.id
    _ensure_tenant_session(request)
    return RedirectResponse(url="/dashboard", status_code=303)


@router.get("/signup", response_class=HTMLResponse)
def signup_page(request: Request, db: Session = Depends(get_db)):
    # If already logged in, go straight to dashboard
    user = get_current_user(request, db)
    if user:
        _ensure_tenant_session(request)
        return RedirectResponse(url="/dashboard", status_code=303)

    return templates.TemplateResponse("signup.html", {"request": request, "error_html": None})


@router.post("/signup", response_class=HTMLResponse)
def signup(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    # ── New fields from 2-step wizard (all optional for backward compat) ──
    confirm_password: str = Form(""),
    full_name: str = Form(""),
    join_mode: str = Form("new"),        # "new" or "join"
    org_name: str = Form(""),
    org_slug: str = Form(""),
    industry: str = Form(""),
    team_size: str = Form(""),
    invite_code: str = Form(""),
    db: Session = Depends(get_db),
):
    email = (email or "").strip().lower()

    if "@" not in email or len(email) > 320:
        return templates.TemplateResponse(
            "signup.html",
            {"request": request, "error_html": "Enter a valid email."},
            status_code=400,
        )

    # ── Confirm password validation (only if provided by new form) ──
    if confirm_password and password != confirm_password:
        return templates.TemplateResponse(
            "signup.html",
            {"request": request, "error_html": "Passwords do not match."},
            status_code=400,
        )

    pw_err = _password_error(password)
    if pw_err:
        return templates.TemplateResponse(
            "signup.html",
            {"request": request, "error_html": pw_err},
            status_code=400,
        )

    existing = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if existing:
        return templates.TemplateResponse(
            "signup.html",
            {"request": request, "error_html": 'Email already registered. <a href="/login">Login</a>.'},
            status_code=400,
        )

    try:
        pw_hash = hash_password(password)
    except MissingBackendError:
        return templates.TemplateResponse(
            "signup.html",
            {
                "request": request,
                "error_html": "Password backend missing. Install bcrypt: <code>pip install bcrypt</code>",
            },
            status_code=500,
        )
    except ValueError as e:
        msg = str(e)
        if "longer than 72" in msg or "72 bytes" in msg:
            msg = f"Password too long. Max {MAX_BCRYPT_BYTES} bytes (bcrypt limit). Use a shorter password."
            return templates.TemplateResponse(
                "signup.html",
                {"request": request, "error_html": msg},
                status_code=400,
            )
        return templates.TemplateResponse(
            "signup.html",
            {"request": request, "error_html": "Unable to create account due to password hashing error."},
            status_code=500,
        )
    except Exception:
        return templates.TemplateResponse(
            "signup.html",
            {"request": request, "error_html": "Unable to create account due to server error."},
            status_code=500,
        )

    user = User(email=email, password_hash=pw_hash)
    db.add(user)
    db.commit()
    db.refresh(user)

    # ── Create Account + pending Membership for approval flow ──
    is_owner = OWNER_EMAIL and email == OWNER_EMAIL
    try:
        from tenancy.models import Account, Membership, Tenant
        DEFAULT_TENANT_SLUG = os.getenv("DEFAULT_TENANT", "local")

        # Create or get Account
        acct = db.execute(select(Account).where(Account.email == email)).scalar_one_or_none()
        if not acct:
            acct = Account(email=email, password_hash=pw_hash, is_active=True)
            db.add(acct)
            db.commit()
            db.refresh(acct)

        # Get or create default tenant
        tenant = db.execute(select(Tenant).where(Tenant.slug == DEFAULT_TENANT_SLUG)).scalar_one_or_none()
        if not tenant:
            tenant = Tenant(slug=DEFAULT_TENANT_SLUG, name=DEFAULT_TENANT_SLUG.upper(), is_active=True)
            db.add(tenant)
            db.commit()
            db.refresh(tenant)

        # Check if membership already exists
        existing_mem = db.execute(
            select(Membership).where(
                Membership.tenant_id == tenant.id,
                Membership.account_id == acct.id,
            )
        ).scalar_one_or_none()

        if not existing_mem:
            if is_owner:
                # Owner gets active + owner role immediately
                mem = Membership(tenant_id=tenant.id, account_id=acct.id, role="owner", status="active")
            else:
                # Everyone else gets pending status — awaits admin approval
                mem = Membership(tenant_id=tenant.id, account_id=acct.id, role="member", status="pending")
            db.add(mem)
            db.commit()
    except Exception:
        pass  # If tenancy not set up yet, skip

    # ── Store org metadata in session ──
    request.session["signup_full_name"] = (full_name or "").strip()
    request.session["signup_join_mode"] = (join_mode or "new").strip()
    request.session["signup_org_name"] = (org_name or "").strip()
    request.session["signup_org_slug"] = (org_slug or "").strip()
    request.session["signup_industry"] = (industry or "").strip()
    request.session["signup_team_size"] = (team_size or "").strip()
    request.session["signup_invite_code"] = (invite_code or "").strip()

    # ── Do NOT log in pending users — show approval message ──
    if not is_owner:
        return templates.TemplateResponse(
            "signup.html",
            {
                "request": request,
                "error_html": '<div style="color:#22c55e;">&#10003; Account created successfully! '
                              'Your account is awaiting admin approval. '
                              'You will be notified once approved.</div>',
            },
        )

    # Owner gets logged in immediately
    request.session[SESSION_KEY] = user.id
    _ensure_tenant_session(request)
    return RedirectResponse(url="/agent-ui", status_code=303)


@router.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    _ensure_tenant_session(request)
    return templates.TemplateResponse("dashboard.html", {"request": request, "user": user})


# ----------------------------
# Forgot password + reset flow
# ----------------------------

@router.get("/forgot-password", response_class=HTMLResponse)
def forgot_password_page(request: Request):
    return templates.TemplateResponse(
        "forgot_password.html",
        {"request": request, "info_html": None, "reset_link": None},
    )


@router.post("/forgot-password", response_class=HTMLResponse)
def forgot_password_submit(
    request: Request,
    email: str = Form(...),
    db: Session = Depends(get_db),
):
    email = (email or "").strip().lower()

    # SAFE: don't reveal whether email exists
    info_html = "If an account exists for that email, a reset link will be provided."

    user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()

    reset_link = None
    if user:
        raw_token = secrets.token_urlsafe(32)
        token_hash = _hash_token(raw_token)

        now = dt.datetime.utcnow()
        expires_at = now + dt.timedelta(minutes=RESET_TTL_MINUTES)

        db.add(
            PasswordResetToken(
                user_id=user.id,
                token_hash=token_hash,
                created_at=now,
                expires_at=expires_at,
                used_at=None,
            )
        )
        db.commit()

        base = str(request.base_url).rstrip("/")
        reset_link = f"{base}/reset-password?token={raw_token}"

        if SHOW_RESET_LINK:
            info_html = f'Reset link (demo): <a href="{reset_link}">Click here</a>'

    return templates.TemplateResponse(
        "forgot_password.html",
        {"request": request, "info_html": info_html, "reset_link": reset_link if SHOW_RESET_LINK else None},
    )


@router.get("/reset-password", response_class=HTMLResponse)
def reset_password_page(
    request: Request,
    token: str,
    db: Session = Depends(get_db),
):
    token_hash = _hash_token(token)
    now = dt.datetime.utcnow()

    row = db.execute(
        select(PasswordResetToken).where(PasswordResetToken.token_hash == token_hash)
    ).scalar_one_or_none()

    if (not row) or row.used_at is not None or row.expires_at < now:
        return templates.TemplateResponse(
            "reset_password.html",
            {
                "request": request,
                "token": None,
                "error_html": 'Invalid or expired link. <a href="/forgot-password">Try again</a>.',
            },
            status_code=400,
        )

    return templates.TemplateResponse(
        "reset_password.html",
        {"request": request, "token": token, "error_html": None},
    )


@router.post("/reset-password", response_class=HTMLResponse)
def reset_password_submit(
    request: Request,
    token: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    pw_err = _password_error(password)
    if pw_err:
        return templates.TemplateResponse(
            "reset_password.html",
            {"request": request, "token": token, "error_html": pw_err},
            status_code=400,
        )

    token_hash = _hash_token(token)
    now = dt.datetime.utcnow()

    row = db.execute(
        select(PasswordResetToken).where(PasswordResetToken.token_hash == token_hash)
    ).scalar_one_or_none()

    if (not row) or row.used_at is not None or row.expires_at < now:
        return templates.TemplateResponse(
            "reset_password.html",
            {
                "request": request,
                "token": None,
                "error_html": 'Invalid or expired link. <a href="/forgot-password">Try again</a>.',
            },
            status_code=400,
        )

    user = db.get(User, row.user_id)
    if not user:
        return templates.TemplateResponse(
            "reset_password.html",
            {
                "request": request,
                "token": None,
                "error_html": 'Invalid link. <a href="/forgot-password">Try again</a>.',
            },
            status_code=400,
        )

    try:
        user.password_hash = hash_password(password)
    except MissingBackendError:
        return templates.TemplateResponse(
            "reset_password.html",
            {
                "request": request,
                "token": token,
                "error_html": "Password backend missing. Install bcrypt: <code>pip install bcrypt</code>",
            },
            status_code=500,
        )
    except Exception:
        return templates.TemplateResponse(
            "reset_password.html",
            {"request": request, "token": token, "error_html": "Password reset failed due to server error."},
            status_code=500,
        )

    row.used_at = now
    db.commit()

    request.session[SESSION_KEY] = user.id
    _ensure_tenant_session(request)
    return RedirectResponse(url="/dashboard", status_code=303)


# ─── Platform God Bootstrap ───

from pydantic import BaseModel as _BM

class _BootstrapRequest(_BM):
    secret: str
    email: str = ""


@router.post("/bootstrap-admin", response_class=HTMLResponse)
def bootstrap_admin(
    request: Request,
    req: _BootstrapRequest = None,
    secret: str = Form(None),
    email: str = Form(None),
    db: Session = Depends(get_db),
):
    """
    Emergency bootstrap: create/restore Platform God account.
    Protected by ADMIN_BOOTSTRAP_KEY from .env.
    Can be called N times — always restores god access.

    Accepts both JSON and form POST.
    """
    import json as _json

    # Handle both JSON body and form data
    _secret = secret
    _email = email
    if req and not _secret:
        _secret = req.secret
        _email = req.email

    if not ADMIN_BOOTSTRAP_KEY or not _secret:
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error_html": "Bootstrap not configured."},
            status_code=400,
        )

    # Constant-time comparison to prevent timing attacks
    import hmac as _hmac
    if not _hmac.compare_digest(_secret.strip(), ADMIN_BOOTSTRAP_KEY):
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error_html": "Invalid bootstrap key."},
            status_code=403,
        )

    god_email = (_email or PLATFORM_GOD_EMAIL or OWNER_EMAIL or "").strip().lower()
    if not god_email:
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error_html": "No email specified and PLATFORM_GOD_EMAIL not set."},
            status_code=400,
        )

    try:
        from tenancy.models import Account, Membership, Tenant, PlatformRole

        # Ensure User exists
        user = db.execute(select(User).where(User.email == god_email)).scalar_one_or_none()
        if not user:
            # Create user with a temp password — they should reset it
            temp_hash = hash_password("BootstrapTemp!2026")
            user = User(email=god_email, password_hash=temp_hash)
            db.add(user)
            db.commit()
            db.refresh(user)

        # Ensure Account exists
        acct = db.execute(select(Account).where(Account.email == god_email)).scalar_one_or_none()
        if not acct:
            acct = Account(email=god_email, password_hash=user.password_hash, is_active=True)
            db.add(acct)
            db.commit()
            db.refresh(acct)

        # Ensure default tenant exists
        DEFAULT_TENANT_SLUG = os.getenv("DEFAULT_TENANT", "local")
        tenant = db.execute(select(Tenant).where(Tenant.slug == DEFAULT_TENANT_SLUG)).scalar_one_or_none()
        if not tenant:
            tenant = Tenant(slug=DEFAULT_TENANT_SLUG, name=DEFAULT_TENANT_SLUG.upper(), is_active=True)
            db.add(tenant)
            db.commit()
            db.refresh(tenant)

        # Ensure owner membership (create or fix)
        mem = db.execute(
            select(Membership).where(
                Membership.tenant_id == tenant.id,
                Membership.account_id == acct.id,
            )
        ).scalar_one_or_none()

        if mem:
            mem.role = "owner"
            mem.status = "active"
        else:
            mem = Membership(tenant_id=tenant.id, account_id=acct.id, role="owner", status="active")
            db.add(mem)

        # Ensure platform super_admin role
        pr = db.execute(select(PlatformRole).where(PlatformRole.account_id == acct.id)).scalar_one_or_none()
        if pr:
            pr.role = "super_admin"
        else:
            pr = PlatformRole(account_id=acct.id, role="super_admin")
            db.add(pr)

        db.commit()

        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error_html":
                f'<div style="color:#22c55e;">&#10003; Platform God restored: {god_email}<br>'
                f'Role: owner + super_admin<br>'
                f'You can now <a href="/login">login</a>.</div>'},
        )

    except Exception as e:
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error_html": f"Bootstrap failed: {str(e)}"},
            status_code=500,
        )
