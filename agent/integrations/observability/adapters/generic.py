"""Generic fallback adapter — used when no vendor-specific adapter matches.

Still useful: runs the closed-loop probe against any observability-shaped
endpoint set, tries the usual auth header names, and lets the round-trip
+ calculation oracles work with best-effort field lookups.
"""
from dataclasses import dataclass, field

from agent.integrations.observability.base import BaseAdapter
from agent.integrations.observability import registry


@dataclass
class GenericAdapter(BaseAdapter):
    name: str = "generic"
    display_name: str = "Generic observability platform"


registry.register(GenericAdapter())
