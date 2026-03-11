"""
tenancy/models_sso.py — SSO Configuration Models
Add to tenancy/models.py or import separately.

Supports per-tenant SSO: each tenant can configure their own
OIDC or SAML provider. When a user hits /auth/sso/<provider>,
the system looks up the tenant's SSO config and redirects.
"""
import uuid
import datetime as dt

from sqlalchemy import (
    Column, String, DateTime, ForeignKey, Boolean, Text, Index, UniqueConstraint
)
from sqlalchemy.orm import relationship

from auth.db import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class SSOConfig(Base):
    """
    Per-tenant SSO provider configuration.
    One tenant can have multiple providers (e.g., Google + SAML).
    Only one can be 'primary' (used for the "Sign in with SSO" button).
    """
    __tablename__ = "sso_configs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "provider_slug", name="uq_sso_tenant_provider"),
        Index("ix_sso_tenant", "tenant_id"),
    )

    id = Column(String(36), primary_key=True, default=_uuid)
    tenant_id = Column(String(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)

    # Provider identification
    provider_slug = Column(String(50), nullable=False)   # "google", "azure-ad", "okta", "saml-corp"
    provider_type = Column(String(20), nullable=False)    # "oidc" or "saml"
    display_name = Column(String(100), nullable=True)     # "Sign in with Google"

    # OIDC fields
    client_id = Column(Text, nullable=True)               # encrypted at rest
    client_secret = Column(Text, nullable=True)           # encrypted at rest
    discovery_url = Column(Text, nullable=True)           # .well-known/openid-configuration
    scopes = Column(String(500), default="openid email profile")
    
    # SAML fields
    idp_entity_id = Column(Text, nullable=True)
    idp_sso_url = Column(Text, nullable=True)
    idp_certificate = Column(Text, nullable=True)         # PEM format, encrypted
    sp_entity_id = Column(Text, nullable=True)            # our SP identifier
    
    # Behavior
    is_primary = Column(Boolean, default=False)           # show on login page
    is_active = Column(Boolean, default=True)
    auto_provision = Column(Boolean, default=True)        # auto-create membership on first login
    default_role = Column(String(30), default="member")   # role for auto-provisioned users
    
    # Metadata
    created_at = Column(DateTime, default=dt.datetime.utcnow)
    updated_at = Column(DateTime, default=dt.datetime.utcnow, onupdate=dt.datetime.utcnow)
    created_by = Column(String(36), ForeignKey("accounts.id"), nullable=True)


class SSOSession(Base):
    """
    Tracks SSO login attempts for security audit.
    Links SSO identity (sub/nameID) to account.
    """
    __tablename__ = "sso_sessions"
    __table_args__ = (
        Index("ix_sso_sessions_account", "account_id"),
        Index("ix_sso_sessions_external", "external_id"),
    )

    id = Column(String(36), primary_key=True, default=_uuid)
    sso_config_id = Column(String(36), ForeignKey("sso_configs.id", ondelete="CASCADE"), nullable=False)
    account_id = Column(String(36), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=True)

    # Identity from IdP
    external_id = Column(String(500), nullable=False)      # OIDC "sub" or SAML NameID
    external_email = Column(String(320), nullable=True)
    external_name = Column(String(200), nullable=True)

    # Session tracking
    state_nonce = Column(String(128), nullable=True)       # CSRF protection
    login_at = Column(DateTime, default=dt.datetime.utcnow)
    ip_address = Column(String(64), nullable=True)
    user_agent = Column(Text, nullable=True)
