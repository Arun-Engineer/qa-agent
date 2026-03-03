"""
src/api/routes/llm_settings.py — LLM Provider config API (Admin/Owner only).

Endpoints:
  GET  /api/llm/info           → current provider, model, available (admin+)
  POST /api/settings/provider  → switch provider (admin+)
  GET  /api/llm/models         → list available models (admin+)
  POST /api/llm/test           → test connection (admin+)
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from typing import Optional

from tenancy.rbac import require_min_tenant_role

router = APIRouter()


class ProviderUpdateRequest(BaseModel):
    provider: str
    model: Optional[str] = None


class TestConnectionRequest(BaseModel):
    provider: str
    model: Optional[str] = None


@router.get("/api/llm/info")
async def llm_info(request: Request, ctx=Depends(require_min_tenant_role("admin"))):
    """Current LLM configuration — Admin/Owner only."""
    from src.llm.provider import (
        get_default_provider, detect_available_providers,
        DEFAULT_MODELS, _load_llm_config,
    )

    active_provider = (request.session.get("active_provider") or "").strip()
    active_model = (request.session.get("active_model") or "").strip()

    resolved_provider = active_provider or get_default_provider()
    resolved_model = active_model or DEFAULT_MODELS.get(resolved_provider, "")

    cfg = _load_llm_config()
    available_models = cfg.get("available_models", {
        "openai": ["gpt-4o-mini", "gpt-4o", "gpt-4-turbo"],
        "anthropic": ["claude-sonnet-4-20250514", "claude-haiku-4-5-20251001"],
    })

    return {
        "current_provider": resolved_provider,
        "current_model": resolved_model,
        "available_providers": detect_available_providers(),
        "available_models": available_models,
        "default_provider": get_default_provider(),
        "session_override": {
            "provider": active_provider or None,
            "model": active_model or None,
        },
    }


@router.post("/api/settings/provider")
async def update_provider(req: ProviderUpdateRequest, request: Request,
                          ctx=Depends(require_min_tenant_role("admin"))):
    """Switch LLM provider — Admin/Owner only."""
    from src.llm.provider import detect_available_providers, DEFAULT_MODELS

    provider = req.provider.strip().lower()
    if provider not in ("openai", "anthropic"):
        raise HTTPException(status_code=400, detail="Provider must be 'openai' or 'anthropic'")

    available = detect_available_providers()
    if provider not in available:
        raise HTTPException(
            status_code=400,
            detail=f"Provider '{provider}' not available. "
                   f"Set {'OPENAI_API_KEY' if provider == 'openai' else 'ANTHROPIC_API_KEY'} in .env. "
                   f"Available: {available}"
        )

    request.session["active_provider"] = provider
    if req.model:
        request.session["active_model"] = req.model
    else:
        request.session["active_model"] = DEFAULT_MODELS.get(provider, "")

    try:
        from auth.db import SessionLocal
        from tenancy.audit import log_audit
        db = SessionLocal()
        try:
            tid = getattr(request.state, "tenant_id", None) or request.session.get("tenant_id")
            aid = request.session.get("account_id")
            log_audit(db, request, tid, aid, "settings.provider.update",
                      {"provider": provider, "model": request.session.get("active_model")})
        finally:
            db.close()
    except Exception:
        pass

    return {
        "active_provider": provider,
        "active_model": request.session.get("active_model"),
    }


@router.get("/api/llm/models")
async def list_models(request: Request, ctx=Depends(require_min_tenant_role("admin"))):
    """List available models — Admin/Owner only."""
    from src.llm.provider import _load_llm_config
    cfg = _load_llm_config()
    return cfg.get("available_models", {
        "openai": ["gpt-4o-mini", "gpt-4o", "gpt-4-turbo"],
        "anthropic": ["claude-sonnet-4-20250514", "claude-haiku-4-5-20251001"],
    })


@router.post("/api/llm/test")
async def test_connection(req: TestConnectionRequest, request: Request,
                          ctx=Depends(require_min_tenant_role("admin"))):
    """Test LLM provider connectivity — Admin/Owner only."""
    from src.llm.provider import get_llm
    try:
        llm = get_llm(provider=req.provider, model=req.model)
        resp = llm.chat(
            messages=[{"role": "user", "content": "Say 'connection successful' in exactly 2 words."}],
            max_tokens=20, temperature=0,
        )
        return {"status": "ok", "provider": req.provider, "model": resp.model,
                "response": resp.content, "tokens": resp.usage.get("total_tokens", 0)}
    except Exception as e:
        return {"status": "error", "provider": req.provider, "error": str(e)}
