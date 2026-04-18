"""agent/discovery/cred_vault.py — Run-scoped credential store.

Rules:
  1. Credentials live in a process-local dict keyed by run_id.
  2. They are NEVER persisted to disk, NEVER logged, NEVER returned from any
     endpoint. The API handlers accept creds and store them here; workflow
     code reads them here; on run completion the bucket is wiped.
  3. Keys inside each bucket are role names ("customer", "admin", etc).
  4. A TTL sweeper auto-removes stale buckets after N minutes of inactivity
     in case a run crashes without calling `clear`.

This is deliberately simple (in-memory) for Phase 1. A future phase can swap
in a Redis-backed vault for multi-worker deployments; the interface won't
change.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Credential:
    role: str
    username: str = ""
    password: str = ""
    totp_seed: str = ""                 # optional, for TOTP-based 2FA
    extras: dict = field(default_factory=dict)   # any other fields the auth plugin needs


@dataclass
class _Bucket:
    creds: dict[str, Credential] = field(default_factory=dict)   # role -> Credential
    pending_prompts: list[dict] = field(default_factory=list)    # [{role, hint, page_url}]
    created_at: float = field(default_factory=time.time)
    last_touched: float = field(default_factory=time.time)


_LOCK = threading.RLock()
_BUCKETS: dict[str, _Bucket] = {}
_TTL_SECONDS = 60 * 60       # 1 hour — a run that does nothing for this long is dead


def _touch(bucket: _Bucket) -> None:
    bucket.last_touched = time.time()


def start_bucket(run_id: str) -> None:
    with _LOCK:
        _BUCKETS.setdefault(run_id, _Bucket())


def set_credential(run_id: str, cred: Credential) -> None:
    with _LOCK:
        b = _BUCKETS.setdefault(run_id, _Bucket())
        b.creds[cred.role] = cred
        # Drop any pending prompt for this role now that it's satisfied.
        b.pending_prompts = [p for p in b.pending_prompts if p.get("role") != cred.role]
        _touch(b)


def get_credential(run_id: str, role: str) -> Optional[Credential]:
    with _LOCK:
        b = _BUCKETS.get(run_id)
        if not b:
            return None
        _touch(b)
        return b.creds.get(role)


def has_credential(run_id: str, role: str) -> bool:
    return get_credential(run_id, role) is not None


def request_credential(run_id: str, role: str, hint: str = "", page_url: str = "") -> None:
    """Called by the workflow when it discovers it needs creds for a role."""
    with _LOCK:
        b = _BUCKETS.setdefault(run_id, _Bucket())
        # Avoid duplicate prompts for the same role.
        if any(p.get("role") == role for p in b.pending_prompts):
            return
        b.pending_prompts.append({
            "role": role,
            "hint": hint,
            "page_url": page_url,
            "requested_at": time.time(),
        })
        _touch(b)


def pending_prompts(run_id: str) -> list[dict]:
    with _LOCK:
        b = _BUCKETS.get(run_id)
        if not b:
            return []
        _touch(b)
        return list(b.pending_prompts)


def clear(run_id: str) -> None:
    """Wipe all creds + prompts for a completed/cancelled/failed run."""
    with _LOCK:
        b = _BUCKETS.pop(run_id, None)
        if not b:
            return
        # Actively zero the credential material before GC.
        for c in b.creds.values():
            c.username = ""
            c.password = ""
            c.totp_seed = ""
            c.extras.clear()
        b.creds.clear()
        b.pending_prompts.clear()


def sweep_expired() -> int:
    """Periodic cleanup — drop buckets untouched longer than _TTL_SECONDS.
    Returns the number of buckets removed."""
    now = time.time()
    removed = 0
    with _LOCK:
        dead = [rid for rid, b in _BUCKETS.items() if now - b.last_touched > _TTL_SECONDS]
        for rid in dead:
            clear(rid)
            removed += 1
    return removed
