"""agent/discovery/app_model.py — Typed shapes for the discovered application.

The `ApplicationModel` is the pivot point for the whole autonomous agent:
Phase 2 produces it, Phase 3 selects profiles based on roles it exposes,
Phase 4 infers oracles from observed patterns, Phase 5 uses its selectors
as seed memory, Phase 6 diffs successive versions for regression detection.

Everything here is plain data (dataclasses) — no runtime behavior — so the
model can be serialized to JSON, stored in the DB, and diffed cheaply.
"""
from __future__ import annotations

import json
import hashlib
from dataclasses import dataclass, field, asdict
from typing import Any, Literal


FormFieldType = Literal[
    "text", "email", "password", "tel", "number", "url", "search",
    "checkbox", "radio", "select", "textarea", "file", "hidden", "submit",
    "date", "unknown",
]


@dataclass
class FormField:
    name: str
    type: FormFieldType = "unknown"
    required: bool = False
    placeholder: str = ""
    label: str = ""


@dataclass
class Form:
    """A form discovered on a page. `action` is the submit target if known."""
    selector: str            # CSS or accessible locator
    action: str = ""
    method: str = "POST"
    fields: list[FormField] = field(default_factory=list)
    purpose_hint: str = ""   # "login" | "signup" | "search" | "checkout" | ""

    def looks_like_login(self) -> bool:
        names = {f.name.lower() for f in self.fields}
        types = {f.type for f in self.fields}
        return "password" in types and any(
            k in names for k in ("username", "email", "user", "login", "phone", "mobile")
        )


@dataclass
class XhrCall:
    """An XHR / fetch / API call observed while exploring."""
    method: str
    url: str
    status: int = 0
    response_content_type: str = ""
    request_body_shape: dict[str, Any] = field(default_factory=dict)
    response_body_shape: dict[str, Any] = field(default_factory=dict)
    observed_on_page: str = ""

    def fingerprint(self) -> str:
        return f"{self.method} {self.url}"


@dataclass
class Route:
    """A page discovered during exploration."""
    url: str
    title: str = ""
    status: int = 200
    forms: list[Form] = field(default_factory=list)
    xhr_calls: list[XhrCall] = field(default_factory=list)
    depth: int = 0
    is_auth_wall: bool = False
    requires_role: str = ""    # "" | "anonymous" | "customer" | "admin" | ...
    links_to: list[str] = field(default_factory=list)
    console_errors: list[str] = field(default_factory=list)
    screenshot_path: str = ""


@dataclass
class Role:
    """An identity the agent can assume. `cred_ref` points into the vault."""
    name: str                   # "customer", "admin", "anonymous"
    discovered_at: str = ""     # URL where this role first became necessary
    auth_plugin: str = "form_login"   # matched to agent/auth/plugins/*
    cred_ref: str = ""          # opaque handle; actual creds live in vault


@dataclass
class ApplicationModel:
    """The structured understanding of an app built from discovery."""
    base_url: str
    title: str = ""
    routes: list[Route] = field(default_factory=list)
    roles: list[Role] = field(default_factory=list)
    api_endpoints: list[XhrCall] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    discovery_budget_used: dict[str, int] = field(default_factory=dict)

    # ── Derived helpers ────────────────────────────────────────────────────

    def auth_walls(self) -> list[Route]:
        return [r for r in self.routes if r.is_auth_wall]

    def needs_credentials(self) -> bool:
        return bool(self.auth_walls())

    def roles_needing_creds(self) -> list[Role]:
        return [r for r in self.roles if r.name != "anonymous" and not r.cred_ref]

    def public_routes(self) -> list[Route]:
        return [r for r in self.routes if not r.is_auth_wall]

    def fingerprint(self) -> str:
        """Stable hash of the structural model — used for drift detection."""
        core = {
            "base_url": self.base_url,
            "routes": sorted(r.url for r in self.routes),
            "api_endpoints": sorted(x.fingerprint() for x in self.api_endpoints),
            "roles": sorted(r.name for r in self.roles),
        }
        blob = json.dumps(core, sort_keys=True).encode("utf-8")
        return hashlib.sha256(blob).hexdigest()[:16]

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ApplicationModel":
        routes = [
            Route(
                **{**r,
                   "forms": [Form(**{**f,
                                     "fields": [FormField(**ff) for ff in f.get("fields", [])]})
                             for f in r.get("forms", [])],
                   "xhr_calls": [XhrCall(**x) for x in r.get("xhr_calls", [])]}
            )
            for r in data.get("routes", [])
        ]
        roles = [Role(**r) for r in data.get("roles", [])]
        endpoints = [XhrCall(**x) for x in data.get("api_endpoints", [])]
        return cls(
            base_url=data["base_url"],
            title=data.get("title", ""),
            routes=routes,
            roles=roles,
            api_endpoints=endpoints,
            notes=data.get("notes", []),
            discovery_budget_used=data.get("discovery_budget_used", {}),
        )
