"""agent/auth — Pluggable authentication strategies for autonomous runs.

The registry maps plugin names (as declared on `Role.auth_plugin`) to
concrete `AuthPlugin` implementations. Add a new auth method by dropping a
module into agent/auth/plugins/ and calling `register()`.
"""
from agent.auth.base import AuthPlugin, AuthResult  # noqa: F401
from agent.auth.registry import register, get, list_plugins  # noqa: F401
# Import plugin modules so they self-register on import.
from agent.auth.plugins import form_login, basic_auth, bearer_token, oauth_redirect, otp_sms  # noqa: F401
