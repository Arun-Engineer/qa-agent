"""Environment Configuration API."""
from fastapi import APIRouter, HTTPException
from src.api.dependencies import get_env_registry

router = APIRouter()


@router.get("/")
async def list_environments():
    """List all configured environments with access rules."""
    registry = get_env_registry()
    return {"environments": registry.list_all(), "total": len(registry.envs)}


@router.get("/{env_name}")
async def get_environment(env_name: str):
    """Get detailed config for a specific environment."""
    registry = get_env_registry()
    cfg = registry.get(env_name)
    if not cfg:
        raise HTTPException(status_code=404, detail=f"Environment '{env_name}' not found")
    return {
        "name": cfg.name, "base_url": cfg.base_url, "api_url": cfg.api_url,
        "access_mode": cfg.access_mode, "data_strategy": cfg.data_strategy,
        "approval_required": cfg.approval_required,
        "session_timeout_minutes": cfg.session_timeout_minutes,
    }
