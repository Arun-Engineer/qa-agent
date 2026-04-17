# tenancy/tenant_agent_api.py
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from sqlalchemy import select, delete
from auth.db import get_db, SessionLocal
from tenancy.deps import require_session, require_tenant, get_session_user
from agent.agent_runner import run_agent_from_spec, explain_mode
from tenancy.audit import log_audit
from tenancy.rbac import (
    role_env_allowed,
    role_has_permission,
    available_envs_for_role,
    effective_permissions_for_role,
)

# ensure models are registered BEFORE create_all runs in asgi import path
import tenancy.content_models  # noqa

from tenancy.spec_ingest import (
    create_spec_from_text,
    create_spec_from_upload,
    get_spec,
    get_chunks,
)
from tenancy import rag_store
from agent.chat_orchestrator import (
    get_or_create_conversation,
    add_message,
    maybe_update_summary,
    generate_reply,
)

router = APIRouter(dependencies=[Depends(require_tenant)])

ARTIFACT_BASE_DIR = Path(os.getenv("ARTIFACTS_DIR", str(Path("data") / "logs"))).resolve()
ARTIFACT_BASE_DIR.mkdir(parents=True, exist_ok=True)


def _normalize_env(env: str | None) -> str:
    return str(env or "").upper().strip()


def _require_env_permission(user: dict[str, Any], env: str, permission: str):
    extra_envs = set(user.get("extra_envs") or set())
    extra_perms = set(user.get("extra_perms") or set())
    role = str(user.get("role") or "viewer").lower()

    if not role_env_allowed(role, env, extra_envs):
        raise HTTPException(status_code=403, detail=f"Environment not allowed: {env}")
    if not role_has_permission(role, permission, extra_perms):
        raise HTTPException(status_code=403, detail=f"Missing permission: {permission}")


# -------------------------
# Existing UI helpers (keep your current behavior)
# -------------------------
PUBLIC_HTML_PREFIXES = (
    "/login",
    "/signup",
    "/forgot-password",
    "/reset-password",
    "/static",
    "/docs",
    "/openapi.json",
    "/favicon.ico",
)

def _redirect_to_login(request: Request) -> RedirectResponse:
    next_url = request.url.path
    if request.url.query:
        next_url += f"?{request.url.query}"
    return RedirectResponse(url=f"/login?next={next_url}", status_code=303)

def _is_logged_in(request: Request) -> bool:
    return bool(request.session.get("user_id") or request.session.get("account_id"))

def _find_agent_ui_file() -> Path | None:
    project_root = Path(__file__).resolve().parents[1]
    ui_dir = project_root / "ui"
    for name in ("agent_ui.html", "agent-ui.html", "agent.html", "index.html"):
        p = ui_dir / name
        if p.exists():
            return p
    return None

def _inject_session_watcher(html: str) -> str:
    # cross-tab logout + auth invalidation
    watcher = r"""
<script>
(function () {
  const PUBLIC = ["/login", "/signup", "/forgot-password", "/reset-password"];
  const p = window.location.pathname || "/";
  if (PUBLIC.some(x => p.startsWith(x))) return;

  async function ping() {
    try {
      const r = await fetch("/api/metrics", { credentials: "include" });
      if (r.status === 401 || r.status === 403) window.location.replace("/login");
    } catch (e) {}
  }
  ping();
  setInterval(ping, 4000);
})();
</script>
"""
    if "</body>" in html:
        return html.replace("</body>", watcher + "\n</body>")
    return html + watcher

def _serve_agent_ui(request: Request) -> HTMLResponse | RedirectResponse:
    if not _is_logged_in(request):
        return _redirect_to_login(request)

    ui_file = _find_agent_ui_file()
    if not ui_file:
        return HTMLResponse(
            "<h3>Agent UI file not found</h3><p>Create <code>ui/agent_ui.html</code> (or index.html).</p>",
            status_code=200,
            headers={"Cache-Control": "no-store"},
        )

    html = ui_file.read_text(encoding="utf-8", errors="ignore")
    html = _inject_session_watcher(html)
    return HTMLResponse(html, status_code=200, headers={"Cache-Control": "no-store"})


# -------------------------
# UI routes (HTML)
# -------------------------
# ── Spec-Priority Fix: auto-detect workflow from spec content ──
def _detect_workflow(spec: str, task_type: str = "") -> str:
    """
    Detect the right workflow from spec content.
    Spec content ALWAYS wins over task_type from the UI dropdown.

    Rules (in priority order):
      1. If spec mentions cart/product/checkout/UI actions → ui_test
      2. If spec mentions mobile/OTP/login credentials → ui_test
         (credentials = auth prereq, NOT api test data)
      3. Only use api_test if spec is purely about REST endpoints
         with NO browser/UI signals AND no URL present
    """
    import re as _re
    spec_lower = spec.lower()
    has_url = bool(_re.search(r"https?://", spec))

    # Strong UI signals — any of these overrides task_type
    strong_ui = ["cart", "checkout", "add to cart", "product", "shop",
                 "jiomart", "flipkart", "amazon", "swiggy", "zomato",
                 "click", "button", "page", "navigate", "browser",
                 "playwright", "search result", "category", "coupon",
                 "promo", "quantity", "wishlist", "order"]

    # Credential signals — means auth prereq needed → ui_test
    cred_signals = ["mobile:", "otp:", "phone:", "password:", "login credential",
                    "username:", "use this login", "credentials if required"]

    ui_score  = sum(1 for kw in strong_ui   if kw in spec_lower)
    cred_score = sum(1 for kw in cred_signals if kw in spec_lower)

    api_signals = ["api", "endpoint", "rest", "graphql", "curl",
                   "json response", "status code", "http method",
                   "request body", "response schema"]
    api_score = sum(1 for kw in api_signals if kw in spec_lower)

    # Credentials or UI keywords → always ui_test
    if cred_score > 0 or ui_score > 0 or has_url:
        return "ui_test"

    # Pure API spec with no URL and no UI signals
    if api_score > 0 and not has_url:
        return "api_test"

    return "ui_test"  # safe default
# ──────────────────────────────────────────────────────────────

@router.get("/agent-ui", include_in_schema=False)
def agent_ui(request: Request):
    return _serve_agent_ui(request)

@router.get("/run-spec", include_in_schema=False)
@router.get("/ask-qa", include_in_schema=False)
@router.get("/run-history", include_in_schema=False)
@router.get("/control-center", include_in_schema=False)
def agent_ui_aliases(request: Request):
    return _serve_agent_ui(request)


@router.get("/api/me")
def me(request: Request, user=Depends(get_session_user)):
    env_access = available_envs_for_role(user["role"], user.get("extra_envs"))
    permissions = effective_permissions_for_role(user["role"], user.get("extra_perms"))
    return {
        "account_id": user["account_id"],
        "tenant_id": user["tenant_id"],
        "role": user["role"],
        "permissions": permissions,
        "env_access": env_access,
        "active_env": user["active_env"],
        "active_model": user["active_model"],
    }


@router.post("/api/settings/environment")
async def update_active_environment(request: Request, user=Depends(get_session_user)):
    body = await request.json()
    env = _normalize_env(body.get("environment"))
    if not env:
        raise HTTPException(status_code=400, detail="environment is required")

    if not role_has_permission(user["role"], "settings:environment:update", set(user.get("extra_perms") or [])):
        raise HTTPException(status_code=403, detail="Missing permission: settings:environment:update")
    if not role_env_allowed(user["role"], env, set(user.get("extra_envs") or [])):
        raise HTTPException(status_code=403, detail=f"Environment not allowed: {env}")

    request.session["active_env"] = env

    db = SessionLocal()
    try:
        log_audit(db, request, user["tenant_id"], user["account_id"], "settings.environment.update", {"active_env": env})
    finally:
        db.close()

    return {"active_env": env}


@router.post("/api/settings/model")
async def update_active_model(request: Request, user=Depends(get_session_user)):
    body = await request.json()
    model = str(body.get("model") or "").strip()
    env = _normalize_env(body.get("environment") or user.get("active_env"))
    if not model:
        raise HTTPException(status_code=400, detail="model is required")

    if not env:
        raise HTTPException(status_code=400, detail="environment is required")

    required_perm = "prod:model:update" if env == "PROD" else "settings:model:update"
    _require_env_permission(user, env, required_perm)

    request.session["active_model"] = model

    db = SessionLocal()
    try:
        log_audit(db, request, user["tenant_id"], user["account_id"], "settings.model.update", {"environment": env, "model": model})
    finally:
        db.close()

    return {"active_model": model, "environment": env}


# -------------------------
# API: Health/metrics/runs (keep existing)
# -------------------------
@router.get("/api/metrics")
def metrics(request: Request, session=Depends(require_session)):
    return {"ok": True}

@router.get("/api/runs")
def runs(request: Request, session=Depends(require_session)):
    path = Path("data/runs.json")
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    return []


# -------------------------
# ✅ A1) /api/specs  (upload OR paste)
# -------------------------
@router.post("/api/specs")
async def create_spec_endpoint(
    request: Request,
    db: Session = Depends(get_db),
    session=Depends(require_session),
):
    tenant_id = session.get("tenant_id") or getattr(request.state, "tenant_id", "local")
    account_id = session.get("account_id")

    ctype = (request.headers.get("content-type") or "").lower()

    # multipart: upload
    if "multipart/form-data" in ctype:
        form = await request.form()
        file = form.get("file")
        text = form.get("text")
        embed = str(form.get("embed") or "1").lower() in ("1", "true", "yes")

        if file is not None:
            filename = getattr(file, "filename", None) or "upload.bin"
            mime = getattr(file, "content_type", None)
            data = await file.read()

            try:
                spec_id = create_spec_from_upload(
                    db=db,
                    tenant_id=str(tenant_id),
                    account_id=str(account_id) if account_id else None,
                    filename=filename,
                    mime_type=mime,
                    data=data,
                    source="upload",
                    meta={"uploader": str(account_id) if account_id else None},
                    save_original=True,
                )
            except Exception as e:
                raise HTTPException(status_code=400, detail=str(e))

        else:
            # multipart but only text provided
            if not text or not str(text).strip():
                raise HTTPException(status_code=400, detail="Provide 'file' or 'text'")
            spec_id = create_spec_from_text(
                db=db,
                tenant_id=str(tenant_id),
                account_id=str(account_id) if account_id else None,
                text=str(text),
                source="paste",
                meta={"uploader": str(account_id) if account_id else None},
            )

        # optional: embed chunks into chroma
        if embed and rag_store.rag_available():
            chunks = get_chunks(db, str(tenant_id), spec_id)
            payload = [{"id": c.id, "chunk_index": c.chunk_index, "content": c.content, "meta": {"spec_id": spec_id}} for c in chunks]
            try:
                rag_store.upsert_spec_chunks(str(tenant_id), spec_id, payload)
            except Exception:
                # don't fail spec creation if embeddings fail
                pass

        return {"spec_id": spec_id, "rag_indexed": bool(embed and rag_store.rag_available())}

    # JSON: paste
    try:
        body = await request.json()
    except Exception:
        body = {}

    text = (body.get("text") or "").strip()
    embed = bool(body.get("embed", True))

    if not text:
        raise HTTPException(status_code=400, detail="JSON must include: {text: '...'}")

    spec_id = create_spec_from_text(
        db=db,
        tenant_id=str(tenant_id),
        account_id=str(account_id) if account_id else None,
        text=text,
        source="paste",
        meta={"uploader": str(account_id) if account_id else None},
    )

    if embed and rag_store.rag_available():
        chunks = get_chunks(db, str(tenant_id), spec_id)
        payload = [{"id": c.id, "chunk_index": c.chunk_index, "content": c.content, "meta": {"spec_id": spec_id}} for c in chunks]
        try:
            rag_store.upsert_spec_chunks(str(tenant_id), spec_id, payload)
        except Exception:
            pass

    return {"spec_id": spec_id, "rag_indexed": bool(embed and rag_store.rag_available())}


@router.get("/api/specs/{spec_id}")
def get_spec_endpoint(
    spec_id: str,
    request: Request,
    db: Session = Depends(get_db),
    session=Depends(require_session),
):
    tenant_id = session.get("tenant_id") or getattr(request.state, "tenant_id", "local")
    doc = get_spec(db, str(tenant_id), spec_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Spec not found")

    # don't return raw_text by default (can be huge). pass ?include_text=1
    include_text = (request.query_params.get("include_text") or "").lower() in ("1", "true", "yes")
    out = {
        "id": doc.id,
        "tenant_id": doc.tenant_id,
        "account_id": doc.account_id,
        "source": doc.source,
        "filename": doc.filename,
        "mime_type": doc.mime_type,
        "created_at": doc.created_at.isoformat(),
        "meta": json.loads(doc.meta_json or "{}"),
        "chunk_count": len(get_chunks(db, str(tenant_id), spec_id)),
    }
    if include_text:
        out["raw_text"] = doc.raw_text
    return out


@router.get("/api/specs/{spec_id}/chunks")
def get_spec_chunks_endpoint(
    spec_id: str,
    request: Request,
    db: Session = Depends(get_db),
    session=Depends(require_session),
):
    tenant_id = session.get("tenant_id") or getattr(request.state, "tenant_id", "local")
    chunks = get_chunks(db, str(tenant_id), spec_id)
    return [{"id": c.id, "chunk_index": c.chunk_index, "content": c.content} for c in chunks]


# -------------------------
# ✅ A2) Update /api/run to accept spec OR spec_id
# -------------------------
@router.post("/api/run")
async def run_agent_endpoint(
    request: Request,
    db: Session = Depends(get_db),
    session=Depends(require_session),
):
    """
    Backward compatible:
      - {spec, html, trace} (old)
      - {spec_id, task_type, options, html, trace, use_rag}
    """
    tenant_id = session.get("tenant_id") or getattr(request.state, "tenant_id", "local")
    account_id = session.get("account_id")

    body = await request.json()
    spec = (body.get("spec") or "").strip()
    spec_id = body.get("spec_id")
    task_type = (body.get("task_type") or "generate_testcases").strip()
    options = body.get("options") or {}
    html = bool(body.get("html", False))
    trace = bool(body.get("trace", False))
    use_rag = bool(body.get("use_rag", True))

    # ── Credentials from spec (mobile, OTP, pincode, URL) ────────
    # Read from request body — set as env vars for this run only.
    # Never stored in .env — ephemeral per-request only.
    creds_from_spec = body.get("creds") or {}
    _cred_env_backup = {}
    cred_map = {
        "mobile":  ("JIOMART_PHONE",   "TEST_MOBILE"),
        "otp":     ("JIOMART_OTP",     "TEST_OTP"),
        "pincode": ("JIOMART_PINCODE", "TEST_PINCODE"),
        "url":     ("JIOMART_URL",     "APP_BASE_URL"),
    }
    for cred_key, env_keys in cred_map.items():
        val = str(creds_from_spec.get(cred_key) or "").strip()
        if val:
            for env_key in env_keys:
                _cred_env_backup[env_key] = os.environ.get(env_key, "")
                os.environ[env_key] = val
    run_env = _normalize_env(body.get("environment") or request.session.get("active_env") or "UAT")

    role = str(session.get("role") or request.session.get("role") or "viewer").lower()
    extra_envs = {str(e).upper() for e in (request.session.get("extra_envs") or [])}
    extra_perms = {str(p) for p in (request.session.get("extra_perms") or [])}
    run_perm = "prod:runs:create" if run_env == "PROD" else "runs:create"

    if not role_env_allowed(role, run_env, extra_envs):
        raise HTTPException(status_code=403, detail=f"Environment not allowed: {run_env}")
    if not role_has_permission(role, run_perm, extra_perms):
        raise HTTPException(status_code=403, detail=f"Missing permission: {run_perm}")

    if not spec and spec_id:
        doc = get_spec(db, str(tenant_id), str(spec_id))
        if not doc:
            raise HTTPException(status_code=404, detail="Spec not found")
        spec = doc.raw_text

        # RAG-light: if spec is huge, retrieve top chunks for the task
        if use_rag and len(spec) > 15000:
            q = f"{task_type}. {options.get('query','')} acceptance criteria user flows edge cases test data"
            retrieved = rag_store.query_chunks(str(tenant_id), q, top_k=8, spec_id=str(spec_id)) if rag_store.rag_available() else []
            if retrieved:
                spec = "SPEC EXCERPTS (retrieved for task):\n\n" + "\n\n---\n\n".join([r["content"] for r in retrieved])

    if not spec:
        raise HTTPException(status_code=400, detail="Provide either 'spec' or 'spec_id'")

    workflow_name = _detect_workflow(spec, task_type=task_type)

    # ── Live-progress wiring ───────────────────────────────────────────
    # If the client opened an SSE progress channel with X-Progress-Id,
    # activate the bus so workflow code can emit stage labels.
    progress_id = request.headers.get("X-Progress-Id") or body.get("progress_id")
    if progress_id:
        try:
            from src.agents import progress_bus
            progress_bus.activate(progress_id)
            progress_bus.emit("run_start", workflow_name)
        except Exception:
            progress_id = None

    try:
        result = run_agent_from_spec(
            spec,
            html=html,
            trace=trace,
            workflow_name=workflow_name,
        )
    finally:
        # Close the progress channel (sends sentinel → SSE gen terminates).
        if progress_id:
            try:
                from src.agents import progress_bus
                progress_bus.emit("run_end", workflow_name)
                progress_bus.deactivate()
                progress_bus.close(progress_id)
            except Exception:
                pass
        # Restore env vars after run (creds are ephemeral)
        for env_key, old_val in _cred_env_backup.items():
            if old_val:
                os.environ[env_key] = old_val
            else:
                os.environ.pop(env_key, None)

    db = SessionLocal()
    try:
        log_audit(db, request, str(tenant_id), str(account_id) if account_id else None, "run.create", {"environment": run_env, "task_type": task_type, "spec_id": spec_id})
    finally:
        db.close()

    if isinstance(result, dict):
        result["environment"] = run_env
    return result


# -------------------------
# Chat memory endpoints (buffer + rolling summary)
# -------------------------
@router.post("/api/chat/start")
def chat_start(
    request: Request,
    db: Session = Depends(get_db),
    session=Depends(require_session),
):
    tenant_id = session.get("tenant_id") or getattr(request.state, "tenant_id", "local")
    account_id = session.get("account_id")

    conv = get_or_create_conversation(db, str(tenant_id), str(account_id) if account_id else None, None)
    request.session["active_conversation_id"] = conv.id
    return {"conversation_id": conv.id}


@router.get("/api/chat/history")
def chat_history(
    request: Request,
    db: Session = Depends(get_db),
    session=Depends(require_session),
):
    tenant_id = session.get("tenant_id") or getattr(request.state, "tenant_id", "local")
    conv_id = request.session.get("active_conversation_id")
    if not conv_id:
        return {"conversation_id": None, "summary": "", "messages": []}

    from tenancy.content_models import Conversation, ChatMessage
    conv = db.get(Conversation, conv_id)
    if not conv:
        return {"conversation_id": None, "summary": "", "messages": []}

    msgs = db.execute(
        select(ChatMessage)
        .where(ChatMessage.tenant_id == str(tenant_id), ChatMessage.conversation_id == conv_id)
        .order_by(ChatMessage.created_at.asc())
        .limit(200)
    ).scalars().all()

    return {
        "conversation_id": conv_id,
        "summary": conv.summary or "",
        "messages": [{"role": m.role, "content": m.content, "created_at": m.created_at.isoformat()} for m in msgs],
    }


@router.post("/api/chat/clear")
def chat_clear(
    request: Request,
    db: Session = Depends(get_db),
    session=Depends(require_session),
):
    """Clear stored chat messages for the active conversation (backend memory)."""
    tenant_id = session.get("tenant_id") or getattr(request.state, "tenant_id", "local")
    conv_id = request.session.get("active_conversation_id")
    if not conv_id:
        return {"ok": True, "conversation_id": None}

    from tenancy.content_models import Conversation, ChatMessage

    conv = db.get(Conversation, conv_id)
    if not conv:
        request.session.pop("active_conversation_id", None)
        return {"ok": True, "conversation_id": None}

    db.execute(
        delete(ChatMessage).where(
            ChatMessage.tenant_id == str(tenant_id),
            ChatMessage.conversation_id == conv_id,
        )
    )

    conv.summary = ""
    db.add(conv)
    db.commit()

    return {"ok": True, "conversation_id": conv_id}

@router.post("/api/chat/send")
async def chat_send(
    request: Request,
    db: Session = Depends(get_db),
    session=Depends(require_session),
):
    tenant_id = session.get("tenant_id") or getattr(request.state, "tenant_id", "local")
    account_id = session.get("account_id")

    body = await request.json()
    message = (body.get("message") or "").strip()
    spec_id = body.get("spec_id")  # optional
    use_rag = bool(body.get("use_rag", True))

    if not message:
        raise HTTPException(status_code=400, detail="message is required")

    conv_id = body.get("conversation_id") or request.session.get("active_conversation_id")
    conv = get_or_create_conversation(db, str(tenant_id), str(account_id) if account_id else None, conv_id)
    request.session["active_conversation_id"] = conv.id

    add_message(db, str(tenant_id), str(account_id) if account_id else None, conv.id, "user", message)

    retrieved_text = None
    if spec_id and use_rag:
        if rag_store.rag_available():
            hits = rag_store.query_chunks(str(tenant_id), message, top_k=6, spec_id=str(spec_id))
            if hits:
                retrieved_text = "\n\n---\n\n".join([h["content"] for h in hits])
        else:
            # keyword fallback: use first few chunks
            chunks = get_chunks(db, str(tenant_id), str(spec_id))
            retrieved_text = "\n\n---\n\n".join([c.content for c in chunks[:4]]) if chunks else None

    # update rolling summary occasionally
    maybe_update_summary(db, str(tenant_id), conv.id)

    reply = generate_reply(db, str(tenant_id), str(account_id) if account_id else None, conv.id, message, retrieved_text)

    add_message(db, str(tenant_id), str(account_id) if account_id else None, conv.id, "assistant", reply)

    return {"conversation_id": conv.id, "reply": reply}


# -------------------------
# Keep /api/explain for backward compatibility (UI still works)
# It now uses chat memory implicitly.
# -------------------------
@router.post("/api/explain")
async def explain(
    request: Request,
    db: Session = Depends(get_db),
    session=Depends(require_session),
):
    body = await request.json()
    q = (body.get("question") or "").strip()
    if not q:
        raise HTTPException(status_code=400, detail="question is required")

    # If you want explain to be "chatty", route it through chat_send-like memory
    # but keep exact output format for your current UI.
    answer = explain_mode(q)
    return {"answer": answer}


# -------------------------
# Ticket stub endpoint (future)
# -------------------------
@router.post("/api/tickets/ingest")
async def tickets_ingest_stub(request: Request, session=Depends(require_session)):
    raise HTTPException(status_code=501, detail="Ticket ingestion not configured yet (Jira/AZDO stubs only).")


# -------------------------
# Artifacts download (tenant-safe)
# -------------------------
@router.get("/api/artifacts/{filename}")
def download_artifact(filename: str, request: Request, session=Depends(require_session)):
    safe_name = Path(filename).name
    path = (ARTIFACT_BASE_DIR / safe_name).resolve()

    if ARTIFACT_BASE_DIR not in path.parents and path != ARTIFACT_BASE_DIR:
        raise HTTPException(status_code=400, detail="Invalid filename")
    if not path.exists():
        raise HTTPException(status_code=404, detail="Not Found")

    return FileResponse(path, filename=safe_name)


# -------------------------
# Catch-all SPA fallback (MUST BE LAST)
# -------------------------

# ── Phase 3: LLM Config Page ──
@router.get("/llm-config", include_in_schema=False)
def llm_config_page(request: Request, session=Depends(require_session)):
    """LLM Provider Configuration — Phase 3 admin panel."""
    from pathlib import Path
    from fastapi.responses import HTMLResponse
    html_path = Path("templates/admin.html")
    if not html_path.exists():
        raise HTTPException(status_code=404, detail="admin.html not found in templates/")
    return HTMLResponse(html_path.read_text(encoding="utf-8"))




# ═══════════════════════════════════════════════════════════════
# LLM Config API — GET endpoints (fixes "Failed to load LLM config")
# ═══════════════════════════════════════════════════════════════

import time as _time
import httpx as _httpx

_llm_model_cache = {"openai": [], "ts": 0, "ttl": 3600}


def _load_llm_yaml() -> dict:
    """Load config/llm.yaml — the source of truth for LLM settings."""
    import yaml
    yaml_path = Path("config/llm.yaml")
    if not yaml_path.exists():
        return {"default_provider": "openai", "providers": {}, "available_models": {}}
    try:
        return yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def _fetch_openai_models_live() -> list:
    """Fetch live model list from OpenAI API — includes new models automatically."""
    api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not api_key:
        return []
    try:
        resp = _httpx.get(
            "https://api.openai.com/v1/models",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=6.0,
        )
        resp.raise_for_status()
        raw = resp.json().get("data", [])
        # Keep only chat-suitable models
        exclude = ("embedding", "tts", "whisper", "dall-e", "davinci",
                   "babbage", "ada", "curie", "instruct", "realtime",
                   "audio", "search", "moderation")
        keep_prefix = ("gpt-", "o1", "o2", "o3", "o4", "o5")
        models = [
            m["id"] for m in raw
            if any(m["id"].startswith(p) for p in keep_prefix)
            and not any(ex in m["id"] for ex in exclude)
        ]
        models.sort(reverse=True)
        return models
    except Exception:
        return []


def _get_models_for_provider(provider: str) -> list:
    """Return model list — live from OpenAI API (cached 1h) or from llm.yaml."""
    cfg = _load_llm_yaml()
    yaml_models = cfg.get("available_models", {}).get(provider, [])

    if provider == "openai":
        now = _time.time()
        cache = _llm_model_cache
        if cache["openai"] and (now - cache["ts"]) < cache["ttl"]:
            return cache["openai"]
        live = _fetch_openai_models_live()
        if live:
            cache["openai"] = live
            cache["ts"]     = now
            return live
        # Fallback to yaml list
        return yaml_models or ["gpt-4o-mini", "gpt-4o", "gpt-4-turbo", "o1-mini", "o1"]

    return yaml_models


@router.get("/api/settings/provider")
async def get_llm_config(request: Request, user=Depends(get_session_user)):
    """
    Returns current LLM provider + model config.
    Frontend calls this on Admin page load.
    Previously missing — caused 'Failed to load LLM config: Not Found'.
    """
    cfg      = _load_llm_yaml()
    provider = cfg.get("default_provider", "openai")
    p_cfg    = cfg.get("providers", {}).get(provider, {})
    model    = (
        request.session.get("active_model")
        or p_cfg.get("model")
        or os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    )
    temp = p_cfg.get("temperature", 0.2)
    return {
        "provider":    provider,
        "model":       model,
        "temperature": temp,
        "available_models": _get_models_for_provider(provider),
    }


@router.get("/api/models")
async def list_models(provider: str = "openai", request: Request = None,
                      user=Depends(get_session_user)):
    """
    Returns live model list for provider.
    OpenAI: fetched from API (cached 1h) — new models appear automatically.
    Others: from config/llm.yaml.
    """
    models = _get_models_for_provider(provider)
    source = "live" if provider == "openai" and _llm_model_cache["openai"] else "yaml"
    return {"provider": provider, "models": models, "source": source}


@router.post("/api/models/refresh")
async def refresh_models(provider: str = "openai", user=Depends(get_session_user)):
    """Force-refresh the live model cache."""
    _llm_model_cache["ts"] = 0  # expire cache
    models = _get_models_for_provider(provider)
    return {"provider": provider, "models": models, "refreshed": True}

# ═══════════════════════════════════════════════════════════════


# ── GET /api/llm/info — required by app.js loadLLMConfig() ───

@router.get("/api/llm/model-permissions")
async def get_model_permissions(request: Request, db=Depends(get_db)):
    """Get allowed models for current user's role."""
    session = request.session
    role = str(session.get("role") or "viewer").lower()
    tenant_id = session.get("tenant_id")

    perms_file = Path("data/model_permissions.json")
    if not perms_file.exists():
        return JSONResponse({"role": role, "allowed": {"openai": ["gpt-4o-mini"], "anthropic": []}})

    perms = json.loads(perms_file.read_text(encoding="utf-8"))

    # Check team-specific override first
    team_perms = perms.get("teams", {}).get(str(tenant_id), None)
    if team_perms:
        allowed = team_perms
    else:
        # Fall back to role-based permissions
        allowed = perms.get("roles", {}).get(role, perms["roles"].get("viewer", {}))

    # Fetch live models and filter by allowed list
    openai_live = _fetch_openai_models()
    anthropic_live = _fetch_anthropic_models()

    def filter_models(live, allowed_list):
        if allowed_list == ["*"]:
            return live
        return [m for m in live if any(a in m for a in allowed_list)]

    return JSONResponse({
        "role": role,
        "allowed": {
            "openai": filter_models(openai_live, allowed.get("openai", [])),
            "anthropic": filter_models(anthropic_live, allowed.get("anthropic", []))
        }
    })


@router.post("/api/llm/model-permissions")
async def set_model_permissions(request: Request, db=Depends(get_db)):
    """Admin: set allowed models for a team or role."""
    session = request.session
    role = str(session.get("role") or "viewer").lower()
    if role not in ("super_admin", "admin", "owner"):
        raise HTTPException(status_code=403, detail="Admin access required")

    body = await request.json()
    target_role = body.get("role")
    target_team = body.get("team_id")
    allowed_openai = body.get("openai", [])
    allowed_anthropic = body.get("anthropic", [])

    perms_file = Path("data/model_permissions.json")
    perms = json.loads(perms_file.read_text(encoding="utf-8")) if perms_file.exists() else {"roles": {}, "teams": {}}

    if target_team:
        perms.setdefault("teams", {})[str(target_team)] = {
            "openai": allowed_openai,
            "anthropic": allowed_anthropic
        }
    elif target_role:
        perms.setdefault("roles", {})[target_role] = {
            "openai": allowed_openai,
            "anthropic": allowed_anthropic
        }

    perms_file.write_text(json.dumps(perms, indent=2), encoding="utf-8")
    return JSONResponse({"status": "saved", "permissions": perms})


@router.get("/api/llm/info")
async def get_llm_info(request: Request, user=Depends(get_session_user)):
    """
    Returns current LLM provider, model, and all available models.
    Called by app.js on Admin page load.
    Fetches live model list from OpenAI API (cached 1h) so new models
    like gpt-5.x appear automatically without any code changes.
    """
    import time as _t
    import yaml as _yaml

    # ── Load llm.yaml for defaults + fallback model lists ────
    def _load_yaml():
        p = Path("config/llm.yaml")
        if not p.exists():
            return {}
        try:
            return _yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        except Exception:
            return {}

    # ── Fetch live OpenAI models (cached 1 hour) ─────────────
    _cache = getattr(get_llm_info, "_cache", {"models": [], "ts": 0})
    get_llm_info._cache = _cache

    def _live_openai_models():
        now = _t.time()
        if _cache["models"] and now - _cache["ts"] < 3600:
            return _cache["models"]
        api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
        if not api_key:
            return []
        try:
            import httpx
            resp = httpx.get(
                "https://api.openai.com/v1/models",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=6.0,
            )
            resp.raise_for_status()
            exclude = ("embedding","tts","whisper","dall-e","davinci",
                       "babbage","ada","curie","instruct","realtime",
                       "audio","search","moderation")
            prefixes = ("gpt-","o1","o2","o3","o4","o5")
            models = sorted(
                [m["id"] for m in resp.json().get("data", [])
                 if any(m["id"].startswith(p) for p in prefixes)
                 and not any(e in m["id"] for e in exclude)],
                reverse=True
            )
            _cache["models"] = models
            _cache["ts"]     = now
            return models
        except Exception:
            return []

    cfg      = _load_yaml()
    provider = (
        request.session.get("active_provider")
        or cfg.get("default_provider", "openai")
    )
    p_cfg    = cfg.get("providers", {}).get(provider, {})
    model    = (
        request.session.get("active_model")
        or p_cfg.get("model")
        or os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    )

    # ── Build available_models dict ───────────────────────────
    yaml_openai    = cfg.get("available_models", {}).get("openai", [])
    yaml_anthropic = cfg.get("available_models", {}).get("anthropic", [])

    live_openai = _live_openai_models()
    openai_models = live_openai or yaml_openai or [
        "gpt-4o-mini", "gpt-4o", "gpt-4-turbo",
        "o1-mini", "o1", "o3-mini",
    ]
    anthropic_models = yaml_anthropic or [
        "claude-opus-4-5",
        "claude-sonnet-4-5",
        "claude-haiku-4-5-20251001",
        "claude-sonnet-4-20250514",
        "claude-opus-4-20250514",
    ]

    # ── Detect which providers have API keys configured ───────
    available_providers = []
    if os.getenv("OPENAI_API_KEY"):
        available_providers.append("openai")
    if os.getenv("ANTHROPIC_API_KEY"):
        available_providers.append("anthropic")
    if not available_providers:
        available_providers = [provider]  # show current even if no key

    return {
        "current_provider":  provider,
        "current_model":     model,
        "available_providers": available_providers,
        "available_models": {
            "openai":    openai_models,
            "anthropic": anthropic_models,
        },
        "source": "live" if live_openai else "yaml",
    }
# ─────────────────────────────────────────────────────────────

@router.get("/{path:path}", include_in_schema=False)
def spa_fallback(path: str, request: Request):
    full_path = "/" + (path or "")

    if full_path.startswith(PUBLIC_HTML_PREFIXES) or full_path.startswith("/api"):
        raise HTTPException(status_code=404, detail="Not Found")

    accept = (request.headers.get("accept") or "").lower()
    if "text/html" in accept:
        return _serve_agent_ui(request)

    raise HTTPException(status_code=404, detail="Not Found")