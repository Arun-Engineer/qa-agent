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
    db: Session = Depends(get_db),
):
    email = (email or "").strip().lower()

    if "@" not in email or len(email) > 320:
        return templates.TemplateResponse(
            "signup.html",
            {"request": request, "error_html": "Enter a valid email."},
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
        # bcrypt 72-byte limit shows up here if something slips through
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

    request.session[SESSION_KEY] = user.id
    _ensure_tenant_session(request)
    return RedirectResponse(url="/dashboard", status_code=303)


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