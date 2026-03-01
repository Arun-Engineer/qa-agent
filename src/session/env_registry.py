"""Environment Registry — Loads and validates SIT/UAT/PROD configurations."""
import yaml
from dataclasses import dataclass
from typing import Optional
from pathlib import Path
import structlog

logger = structlog.get_logger()


@dataclass
class EnvConfig:
    """Configuration for a single environment."""
    name: str
    base_url: str
    api_url: str = ""
    access_mode: str = "full"
    data_strategy: str = "generate_freely"
    approval_required: bool = False
    session_timeout_minutes: int = 480
    db_connection: str = ""
    feature_flags: dict = None

    def __post_init__(self):
        if self.feature_flags is None:
            self.feature_flags = {}
        if not self.api_url:
            self.api_url = self.base_url


class EnvRegistry:
    """Loads environment configs from YAML. Single source of truth for all env settings."""

    def __init__(self, config_path: str = "config/environments.yaml"):
        self.envs: dict[str, EnvConfig] = {}
        self.config_path = config_path
        self._load()

    def _load(self):
        """Load environments from YAML file."""
        p = Path(self.config_path)
        if not p.exists():
            logger.warning("env_config_missing", path=self.config_path)
            self._load_defaults()
            return

        try:
            with open(p) as f:
                data = yaml.safe_load(f)

            for env_name, cfg in data.get("environments", {}).items():
                name = cfg.pop("name", env_name)
                self.envs[env_name] = EnvConfig(name=name, **cfg)
                logger.info("env_loaded", env=env_name, url=self.envs[env_name].base_url)
        except Exception as e:
            logger.error("env_config_error", error=str(e))
            self._load_defaults()

    def _load_defaults(self):
        """Load sensible defaults if no config file exists."""
        self.envs = {
            "sit": EnvConfig(name="SIT", base_url="http://localhost:3000", access_mode="full"),
            "uat": EnvConfig(name="UAT", base_url="http://localhost:3001", access_mode="controlled"),
            "prod": EnvConfig(name="PROD", base_url="http://localhost:3002", access_mode="read_only",
                            session_timeout_minutes=30, approval_required=True),
        }
        logger.info("env_defaults_loaded", count=len(self.envs))

    def get(self, env_name: str) -> Optional[EnvConfig]:
        """Get config for a specific environment."""
        return self.envs.get(env_name.lower())

    def list_all(self) -> list[dict]:
        """List all environments with their config."""
        return [
            {"name": name, "base_url": cfg.base_url, "access_mode": cfg.access_mode}
            for name, cfg in self.envs.items()
        ]

    def validate_env(self, env_name: str) -> tuple[bool, str]:
        """Check if an environment exists and is configured."""
        cfg = self.get(env_name)
        if cfg is None:
            available = ", ".join(self.envs.keys())
            return False, f"Environment '{env_name}' not found. Available: {available}"
        return True, "OK"
