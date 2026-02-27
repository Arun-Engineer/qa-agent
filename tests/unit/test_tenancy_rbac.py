from tenancy.rbac import effective_permissions_for_role, available_envs_for_role


def test_effective_permissions_for_member_includes_role_permissions():
    perms = effective_permissions_for_role("member")
    assert "runs:create" in perms
    assert "settings:environment:update" in perms
    assert "prod:runs:create" not in perms


def test_effective_permissions_for_owner_expands_wildcard():
    perms = effective_permissions_for_role("owner")
    assert "prod:runs:create" in perms
    assert "settings:model:update" in perms


def test_available_envs_merges_overrides():
    envs = available_envs_for_role("admin", {"PROD"})
    assert envs == ["PROD", "SIT", "UAT"]
