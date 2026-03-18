# src/api/routes/llm_settings.py
"""
LLM Settings API

Key features:
  - GET /api/models  — fetches LIVE model list from OpenAI API
    so new models (gpt-5.2, gpt-5.3 etc) appear automatically
  - Falls back to a curated static list if OpenAI API is unreachable
  - Caches the live list for 1 hour to avoid hammering the API
  - POST /api/settings/provider  — save provider/model config
  - POST /api/llm/test           — test connection
"""
from __future__ import annotations

import os
import time
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel

log = logging.getLogger(__name__)
router = APIRouter()

# ── Model cache ───────────────────────────────────────────────
_model_cache: Dict[str, Any] = {
    "models": [],
    "fetched_at": 0,
    "ttl": 3600,  # refresh every 1 hour
}

# ── Curated fallback list (used when OpenAI API unreachable) ──
# Updated manually only as a last resort — live fetch is preferred
_FALLBACK_MODELS = {
    "openai": [
        # GPT-4o family
        {"id": "gpt-4o",            "label": "GPT-4o",            "group": "GPT-4o"},
        {"id": "gpt-4o-mini",       "label": "GPT-4o Mini",       "group": "GPT-4o"},
        # GPT-4 family
        {"id": "gpt-4-turbo",       "label": "GPT-4 Turbo",       "group": "GPT-4"},
        {"id": "gpt-4",             "label": "GPT-4",             "group": "GPT-4"},
        # o-series (reasoning)
        {"id": "o1",                "label": "o1",                "group": "Reasoning"},
        {"id": "o1-mini",           "label": "o1 Mini",           "group": "Reasoning"},
        {"id": "o3-mini",           "label": "o3 Mini",           "group": "Reasoning"},
        # GPT-3.5
        {"id": "gpt-3.5-turbo",     "label": "GPT-3.5 Turbo",     "group": "GPT-3.5"},
    ],
    "anthropic": [
        {"id": "claude-opus-4-5",    "label": "Claude Opus 4.5",   "group": "Claude"},
        {"id": "claude-sonnet-4-5",  "label": "Claude Sonnet 4.5", "group": "Claude"},
        {"id": "claude-haiku-3-5",   "label": "Claude Haiku 3.5",  "group": "Claude"},
    ],
}

# ── Models that are suitable for QA automation ────────────────
# We filter the live list to exclude embeddings, audio, image, etc.
_QA_SUITABLE_PREFIXES = ("gpt-", "o1", "o3", "o4", "claude", "gemini")
_EXCLUDED_SUFFIXES = (
    "embedding", "embed", "tts", "whisper", "dall-e",
    "davinci", "babbage", "ada", "curie", "instruct",
    "realtime", "audio", "search", "moderation",
)


def _is_qa_suitable(model_id: str) -> bool:
    mid = model_id.lower()
    if any(mid.endswith(ex) or ex in mid for ex in _EXCLUDED_SUFFIXES):
        return False
    return any(mid.startswith(p) for p in _QA_SUITABLE_PREFIXES)


def _group_for(model_id: str) -> str:
    mid = model_id.lower()
    if mid.startswith("o1") or mid.startswith("o3") or mid.startswith("o4"):
        return "Reasoning"
    if "gpt-4o" in mid:
        return "GPT-4o"
    if "gpt-4" in mid:
        return "GPT-4"
    if "gpt-3" in mid:
        return "GPT-3.5"
    if "claude" in mid:
        return "Claude"
    if "gemini" in mid:
        return "Gemini"
    return "Other"


def _label_for(model_id: str) -> str:
    """Make a human-readable label from model id."""
    label = model_id
    label = label.replace("gpt-", "GPT-").replace("claude-", "Claude ")
    label = label.replace("-", " ").replace("_", " ")
    # Capitalise each word
    return " ".join(w.capitalize() if not w.isupper() else w
                    for w in label.split())


# ── Live fetch from OpenAI ────────────────────────────────────

def _fetch_openai_models_live() -> List[Dict]:
    """
    Calls GET https://api.openai.com/v1/models with the configured API key.
    Returns a filtered, sorted list of QA-suitable chat models.
    Automatically includes any new models OpenAI releases (gpt-5.x etc).
    """
    api_key = (
        os.getenv("OPENAI_API_KEY")
        or os.getenv("OPENAI_KEY")
        or ""
    ).strip()

    if not api_key:
        log.warning("OPENAI_API_KEY not set — using fallback model list")
        return _FALLBACK_MODELS["openai"]

    try:
        import httpx
        resp = httpx.get(
            "https://api.openai.com/v1/models",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=8.0,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        log.warning(f"OpenAI model list fetch failed: {e} — using fallback")
        return _FALLBACK_MODELS["openai"]

    raw_models = data.get("data", [])

    # Filter to QA-suitable chat models only
    suitable = [
        m for m in raw_models
        if _is_qa_suitable(m.get("id", ""))
    ]

    # Sort: newest / most capable first (lexicographic on id works well for GPT naming)
    suitable.sort(key=lambda m: m["id"], reverse=True)

    result = [
        {
            "id":    m["id"],
            "label": _label_for(m["id"]),
            "group": _group_for(m["id"]),
        }
        for m in suitable
    ]

    log.info(f"Fetched {len(result)} QA-suitable models live from OpenAI")
    return result if result else _FALLBACK_MODELS["openai"]


def get_models_for_provider(provider: str) -> List[Dict]:
    """
    Returns model list for a provider.
    For OpenAI: fetches live from API with 1-hour cache.
    For others: returns curated fallback list.
    """
    provider = (provider or "openai").lower()

    if provider == "openai":
        now = time.time()
        cached = _model_cache
        if cached["models"] and (now - cached["fetched_at"]) < cached["ttl"]:
            return cached["models"]

        # Fetch live
        models = _fetch_openai_models_live()
        _model_cache["models"]     = models
        _model_cache["fetched_at"] = now
        return models

    # Non-OpenAI providers — return fallback
    return _FALLBACK_MODELS.get(provider, [])


# ── API Routes ────────────────────────────────────────────────

class ProviderConfig(BaseModel):
    provider: str = "openai"
    model: str    = "gpt-4o-mini"
    temperature: float = 0.2
    api_key: Optional[str] = None


class LLMTestRequest(BaseModel):
    provider: str = "openai"
    model: str    = "gpt-4o-mini"


@router.get("/api/models")
def list_models(provider: str = "openai"):
    """
    Returns live model list for the given provider.
    OpenAI list is fetched fresh from their API and cached for 1 hour.
    Any new model OpenAI releases will appear here automatically.
    """
    models = get_models_for_provider(provider)
    return {
        "provider": provider,
        "models":   models,
        "count":    len(models),
        "source":   "live" if provider == "openai" else "static",
        "cache_ttl_seconds": _model_cache["ttl"],
    }


@router.post("/api/models/refresh")
def refresh_models(provider: str = "openai"):
    """Force-refresh the model cache (bypass TTL)."""
    _model_cache["fetched_at"] = 0  # expire cache
    models = get_models_for_provider(provider)
    return {
        "provider": provider,
        "models":   models,
        "count":    len(models),
        "refreshed": True,
    }


@router.post("/api/settings/provider")
def save_provider_config(config: ProviderConfig):
    """Save LLM provider + model selection."""
    # Persist to env / settings file
    settings_path = Path("data/llm_settings.json")
    settings_path.parent.mkdir(parents=True, exist_ok=True)

    existing = {}
    if settings_path.exists():
        try:
            existing = json.loads(settings_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    existing.update({
        "provider":    config.provider,
        "model":       config.model,
        "temperature": config.temperature,
    })

    # Store API key in env (never write to disk)
    if config.api_key:
        os.environ["OPENAI_API_KEY"] = config.api_key
        # Bust model cache so next fetch uses new key
        _model_cache["fetched_at"] = 0

    settings_path.write_text(
        json.dumps(existing, indent=2), encoding="utf-8"
    )

    # Apply to running process
    os.environ["OPENAI_MODEL"] = config.model

    return {
        "status":   "saved",
        "provider": config.provider,
        "model":    config.model,
    }


@router.post("/api/llm/test")
def test_llm_connection(req: LLMTestRequest):
    """Send a minimal test prompt to verify the connection works."""
    try:
        from agent.utils.openai_wrapper import chat_completion
        resp = chat_completion(
            model=req.model,
            messages=[{"role": "user", "content": "Reply with the word CONNECTED only."}],
            max_tokens=10,
            temperature=0,
            service_name="llm-connection-test",
        )
        reply = (resp.choices[0].message.content or "").strip()
        return {"status": "ok", "model": req.model, "reply": reply}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
