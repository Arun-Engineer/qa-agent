"""agent/memory/platform_profiles.py — Per-platform, per-tenant memory.

When the Puvi probe (or any platform-specific workflow) discovers a working
ingest path, auth header convention, aggregate endpoint, or dashboard URL
template, it stashes that knowledge here. The next run loads the profile
first and skips the discovery dance.

Stored as a SQLite row keyed on (tenant_id, base_url). The value is a free
JSON blob so platform workflows can evolve their schema without migrations.
Every write bumps ``learned_count`` so the UI can show "the agent has
learned N things about this site".

This is cross-cutting memory — not tied to the oracle/baseline system.
Think of it as the agent's notebook: "last time I tested this site, the
ingest endpoint was at /v1/traces and my emitted trace count matched
their reported count within 0.2%". Next run uses that as its starting
belief instead of re-deriving it.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Optional


_DB_PATH = Path(os.getenv("AUTO_PLATFORM_DB", "data/logs/platform_profiles.db"))
_LOCK = threading.RLock()


def _conn() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(_DB_PATH))
    c.execute("""
        CREATE TABLE IF NOT EXISTS platform_profiles (
            tenant_id    TEXT NOT NULL,
            base_url     TEXT NOT NULL,
            platform     TEXT NOT NULL,
            profile_json TEXT NOT NULL,
            learned_count INTEGER NOT NULL DEFAULT 0,
            first_seen    REAL NOT NULL,
            last_updated  REAL NOT NULL,
            PRIMARY KEY (tenant_id, base_url)
        )
    """)
    return c


def load(tenant_id: str, base_url: str) -> dict[str, Any]:
    """Return the stored profile dict, or {} if none yet. Never raises."""
    with _LOCK:
        try:
            with _conn() as c:
                row = c.execute(
                    "SELECT profile_json, platform, learned_count, "
                    "first_seen, last_updated "
                    "FROM platform_profiles "
                    "WHERE tenant_id=? AND base_url=?",
                    (tenant_id, base_url),
                ).fetchone()
        except Exception:
            return {}
    if not row:
        return {}
    try:
        profile = json.loads(row[0])
    except Exception:
        profile = {}
    profile.setdefault("_meta", {})
    profile["_meta"].update({
        "platform": row[1], "learned_count": row[2],
        "first_seen": row[3], "last_updated": row[4],
    })
    return profile


def update(tenant_id: str, base_url: str, *, platform: str,
           patch: dict[str, Any]) -> dict[str, Any]:
    """Merge ``patch`` into the stored profile (shallow merge) and persist.

    Passing ``{"ingest_path": "/v1/traces"}`` doesn't wipe existing keys
    like ``list_path``. Returns the merged profile.
    """
    now = time.time()
    with _LOCK:
        existing = load(tenant_id, base_url)
        # Strip the read-side _meta so we don't round-trip it.
        existing.pop("_meta", None)

        merged = {**existing, **patch}
        merged_json = json.dumps(merged, sort_keys=True, default=str)

        with _conn() as c:
            row = c.execute(
                "SELECT learned_count, first_seen FROM platform_profiles "
                "WHERE tenant_id=? AND base_url=?",
                (tenant_id, base_url),
            ).fetchone()
            if row:
                new_count = row[0] + _count_new_keys(existing, patch)
                first_seen = row[1]
                c.execute(
                    "UPDATE platform_profiles SET profile_json=?, "
                    "platform=?, learned_count=?, last_updated=? "
                    "WHERE tenant_id=? AND base_url=?",
                    (merged_json, platform, new_count, now,
                     tenant_id, base_url),
                )
            else:
                first_seen = now
                new_count = max(1, len(patch))
                c.execute(
                    "INSERT INTO platform_profiles "
                    "(tenant_id, base_url, platform, profile_json, "
                    " learned_count, first_seen, last_updated) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (tenant_id, base_url, platform, merged_json,
                     new_count, first_seen, now),
                )
    merged["_meta"] = {"platform": platform, "learned_count": new_count,
                       "first_seen": first_seen, "last_updated": now}
    return merged


def _count_new_keys(existing: dict, patch: dict) -> int:
    """Count keys in patch that are either new or changed — what 'new
    knowledge' means for the learned-count display."""
    n = 0
    for k, v in patch.items():
        if k.startswith("_"):
            continue
        if existing.get(k) != v:
            n += 1
    return n


def list_for_tenant(tenant_id: str) -> list[dict]:
    """List every platform profile this tenant has learned — powers the
    'what has the agent learned' view in the UI."""
    with _LOCK:
        with _conn() as c:
            rows = c.execute(
                "SELECT base_url, platform, profile_json, learned_count, "
                "first_seen, last_updated "
                "FROM platform_profiles WHERE tenant_id=? "
                "ORDER BY last_updated DESC",
                (tenant_id,),
            ).fetchall()
    out = []
    for r in rows:
        try:
            profile = json.loads(r[2])
        except Exception:
            profile = {}
        out.append({
            "base_url": r[0], "platform": r[1], "profile": profile,
            "learned_count": r[3], "first_seen": r[4],
            "last_updated": r[5],
        })
    return out


def forget(tenant_id: str, base_url: str) -> bool:
    """Wipe a profile — used when the agent's stored beliefs are clearly
    stale (e.g. Puvi changed their ingest contract)."""
    with _LOCK:
        with _conn() as c:
            cur = c.execute(
                "DELETE FROM platform_profiles "
                "WHERE tenant_id=? AND base_url=?",
                (tenant_id, base_url),
            )
            return cur.rowcount > 0
