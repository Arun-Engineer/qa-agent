"""Tests for Environment Registry."""
from src.session.env_registry import EnvRegistry


class TestEnvRegistry:
    def test_loads_from_yaml(self):
        reg = EnvRegistry("config/environments.yaml")
        assert "sit" in reg.envs
        assert "uat" in reg.envs
        assert "prod" in reg.envs

    def test_get_existing_env(self):
        reg = EnvRegistry("config/environments.yaml")
        cfg = reg.get("sit")
        assert cfg is not None
        assert cfg.access_mode == "full"

    def test_get_nonexistent_env(self):
        reg = EnvRegistry("config/environments.yaml")
        assert reg.get("staging") is None

    def test_validate_valid_env(self):
        reg = EnvRegistry("config/environments.yaml")
        valid, msg = reg.validate_env("uat")
        assert valid is True

    def test_validate_invalid_env(self):
        reg = EnvRegistry("config/environments.yaml")
        valid, msg = reg.validate_env("staging")
        assert valid is False
        assert "not found" in msg

    def test_list_all(self):
        reg = EnvRegistry("config/environments.yaml")
        envs = reg.list_all()
        assert len(envs) >= 3

    def test_falls_back_to_defaults(self):
        reg = EnvRegistry("nonexistent.yaml")
        assert len(reg.envs) == 3  # sit, uat, prod defaults
