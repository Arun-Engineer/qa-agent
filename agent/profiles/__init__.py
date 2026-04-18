"""agent/profiles — Execution profiles for autonomous runs.

A profile bundles {tenant, role, env, data, feature_flags, auth_ref} so a
single run can pivot between identities cleanly. See execution_profile.py.
"""
from agent.profiles.execution_profile import ExecutionProfile, profile_for_role  # noqa: F401
