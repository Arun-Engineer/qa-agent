"""Environment Configuration API — List and inspect environments."""
from fastapi import APIRouter, HTTPException
from src.api.dependencies import get_env_registry

router = APIRouter()


@router.get("/")
async def list_environments():
    registry = get_env_registry()
    envs = []
    for name, cfg in registry.envs.items():
        envs.append({
            "name": name,
            "base_url": cfg.base_url,
            "api_url": cfg.api_url,
            "access_mode": cfg.access_mode,
            "data_strategy": cfg.data_strategy,
            "approval_required": cfg.approval_required,
            "session_timeout_minutes": cfg.session_timeout_minutes,
        })
    return {"environments": envs, "total": len(envs)}


@router.get("/{env_name}")
async def get_environment(env_name: str):
    registry = get_env_registry()
    cfg = registry.get(env_name)
    if not cfg:
        valid, msg = registry.validate_env(env_name)
        raise HTTPException(status_code=404, detail=msg)
    return {
        "name": cfg.name,
        "base_url": cfg.base_url,
        "api_url": cfg.api_url,
        "access_mode": cfg.access_mode,
        "data_strategy": cfg.data_strategy,
        "approval_required": cfg.approval_required,
        "session_timeout_minutes": cfg.session_timeout_minutes,
    }
