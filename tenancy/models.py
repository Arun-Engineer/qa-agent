import uuid
import datetime as dt

from sqlalchemy import (
    Column, String, DateTime, ForeignKey, UniqueConstraint, Boolean, Text, Index
)
from sqlalchemy.orm import relationship

from auth.db import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class Tenant(Base):
    __tablename__ = "tenants"

    id = Column(String(36), primary_key=True, default=_uuid)
    slug = Column(String(63), unique=True, index=True, nullable=False)
    name = Column(String(200), nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=dt.datetime.utcnow, nullable=False)

    memberships = relationship("Membership", back_populates="tenant", cascade="all, delete-orphan")


class Account(Base):
    __tablename__ = "accounts"

    id = Column(String(36), primary_key=True, default=_uuid)
    email = Column(String(320), unique=True, index=True, nullable=False)
    password_hash = Column(Text, nullable=False)

    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=dt.datetime.utcnow, nullable=False)

    memberships = relationship("Membership", back_populates="account", cascade="all, delete-orphan")
    platform_role = relationship("PlatformRole", back_populates="account", uselist=False, cascade="all, delete-orphan")


class PlatformRole(Base):
    __tablename__ = "platform_roles"
    __table_args__ = (UniqueConstraint("account_id", name="uq_platform_roles_account_id"),)

    id = Column(String(36), primary_key=True, default=_uuid)
    account_id = Column(String(36), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True)

    role = Column(String(30), default="none", nullable=False)
    created_at = Column(DateTime, default=dt.datetime.utcnow, nullable=False)

    account = relationship("Account", back_populates="platform_role")


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (
        UniqueConstraint("tenant_id", "account_id", name="uq_membership_tenant_account"),
        Index("ix_memberships_tenant_account", "tenant_id", "account_id"),
    )

    id = Column(String(36), primary_key=True, default=_uuid)
    tenant_id = Column(String(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    account_id = Column(String(36), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)

    role = Column(String(30), default="member", nullable=False)   # owner|admin|member|viewer
    status = Column(String(20), default="active", nullable=False) # active|invited|disabled
    created_at = Column(DateTime, default=dt.datetime.utcnow, nullable=False)

    tenant = relationship("Tenant", back_populates="memberships")
    account = relationship("Account", back_populates="memberships")


class Invite(Base):
    __tablename__ = "invites"
    __table_args__ = (
        UniqueConstraint("tenant_id", "email", name="uq_invite_tenant_email"),
        Index("ix_invites_email", "email"),
    )

    id = Column(String(36), primary_key=True, default=_uuid)
    tenant_id = Column(String(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)

    email = Column(String(320), nullable=False)
    role = Column(String(30), default="member", nullable=False)

    token_hash = Column(String(64), nullable=False, index=True)
    expires_at = Column(DateTime, nullable=False)
    accepted_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=dt.datetime.utcnow, nullable=False)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_tenant_time", "tenant_id", "created_at"),)

    id = Column(String(36), primary_key=True, default=_uuid)
    tenant_id = Column(String(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)

    actor_account_id = Column(String(36), ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True)
    action = Column(String(80), nullable=False)
    ip = Column(String(64), nullable=True)
    user_agent = Column(Text, nullable=True)
    meta_json = Column(Text, nullable=True)

    created_at = Column(DateTime, default=dt.datetime.utcnow, nullable=False)

class TenantDomain(Base):
    """Custom domain mapping — imported by tenancy/resolve.py."""
    __tablename__ = "tenant_domains"

    id = Column(String(36), primary_key=True, default=_uuid)
    tenant_id = Column(String(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    domain = Column(String(255), unique=True, nullable=False, index=True)
    is_verified = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=dt.datetime.utcnow, nullable=False)
