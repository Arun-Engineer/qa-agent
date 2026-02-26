# tenancy/resolve.py
import os
from typing import Optional, Tuple
from sqlalchemy.orm import Session
from sqlalchemy import select

from tenancy.models import Tenant, TenantDomain

BASE_DOMAIN = os.getenv("BASE_DOMAIN", "localhost")  # in prod: yourapp.com


def split_host(host: str) -> Tuple[str, str]:
    """
    returns (hostname_without_port, port_or_empty)
    """
    host = (host or "").strip()
    if ":" in host and host.count(":") == 1:
        h, p = host.split(":")
        return h.lower(), p
    return host.lower(), ""


def is_apex_host(hostname: str) -> bool:
    # apex = yourapp.com or localhost
    return hostname == BASE_DOMAIN or hostname == f"www.{BASE_DOMAIN}"


def extract_subdomain(hostname: str) -> Optional[str]:
    """
    acme.yourapp.com -> acme
    acme.localhost -> acme (works in local dev!)
    """
    if is_apex_host(hostname):
        return None

    # If BASE_DOMAIN is localhost, treat *.localhost as tenant
    suffix = f".{BASE_DOMAIN}"
    if hostname.endswith(suffix):
        sub = hostname[: -len(suffix)]
        if sub and "." not in sub:
            return sub
    return None


def resolve_tenant(db: Session, host: str) -> Optional[Tenant]:
    hostname, _port = split_host(host)

    # 1) apex: no tenant
    if is_apex_host(hostname):
        return None

    # 2) custom domains lookup (optional)
    domain_row = db.execute(select(TenantDomain).where(TenantDomain.domain == hostname)).scalar_one_or_none()
    if domain_row:
        return db.get(Tenant, domain_row.tenant_id)

    # 3) subdomain lookup
    slug = extract_subdomain(hostname)
    if not slug:
        return None
    return db.execute(select(Tenant).where(Tenant.slug == slug, Tenant.is_active == True)).scalar_one_or_none()