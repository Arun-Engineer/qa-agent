"""agent/memory/selector_memory.py — Learned selectors for self-healing UI.

Keyed by `(tenant_id, url_pattern, element_semantic)`, stores the last
known-good selector plus history. The self-healing UI workflow consults
this before generating new selectors, and writes back after a successful
relocate.

Schema (SQLite):
    CREATE TABLE selector_memory (
        tenant_id TEXT NOT NULL,
        url_pattern TEXT NOT NULL,   -- e.g. "/checkout*" or exact URL
        semantic TEXT NOT NULL,      -- e.g. "primary_cta_button"
        selector TEXT NOT NULL,      -- last known-good CSS/XPath
        attempt_count INTEGER DEFAULT 1,
        success_count INTEGER DEFAULT 1,
        last_success_ts REAL DEFAULT 0,
        last_failure_ts REAL DEFAULT 0,
        updated_at REAL NOT NULL,
        PRIMARY KEY (tenant_id, url_pattern, semantic)
    );

Thread-safe: each call opens its own connection.
"""
from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path
from typing import Optional


_DB_PATH = Path(os.getenv("AUTO_SELECTOR_MEMORY_DB",
                          "data/logs/selector_memory.sqlite"))


def _conn() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(_DB_PATH), timeout=5)
    c.execute("""
        CREATE TABLE IF NOT EXISTS selector_memory (
            tenant_id TEXT NOT NULL,
            url_pattern TEXT NOT NULL,
            semantic TEXT NOT NULL,
            selector TEXT NOT NULL,
            attempt_count INTEGER DEFAULT 1,
            success_count INTEGER DEFAULT 1,
            last_success_ts REAL DEFAULT 0,
            last_failure_ts REAL DEFAULT 0,
            updated_at REAL NOT NULL,
            PRIMARY KEY (tenant_id, url_pattern, semantic)
        )
    """)
    return c


def remember(tenant_id: str, url_pattern: str, semantic: str,
             selector: str, *, succeeded: bool = True) -> None:
    now = time.time()
    c = _conn()
    try:
        cur = c.execute("""
            SELECT attempt_count, success_count
            FROM selector_memory
            WHERE tenant_id=? AND url_pattern=? AND semantic=?
        """, (tenant_id, url_pattern, semantic))
        row = cur.fetchone()
        if row is None:
            c.execute("""
                INSERT INTO selector_memory
                    (tenant_id, url_pattern, semantic, selector,
                     attempt_count, success_count,
                     last_success_ts, last_failure_ts, updated_at)
                VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?)
            """, (tenant_id, url_pattern, semantic, selector,
                  1 if succeeded else 0,
                  now if succeeded else 0,
                  0 if succeeded else now, now))
        else:
            attempt, success = row
            c.execute("""
                UPDATE selector_memory
                SET selector=?, attempt_count=?, success_count=?,
                    last_success_ts=?, last_failure_ts=?, updated_at=?
                WHERE tenant_id=? AND url_pattern=? AND semantic=?
            """, (
                selector,
                attempt + 1,
                success + (1 if succeeded else 0),
                now if succeeded else 0,
                now if not succeeded else 0,
                now,
                tenant_id, url_pattern, semantic,
            ))
        c.commit()
    finally:
        c.close()


def recall(tenant_id: str, url_pattern: str,
           semantic: str) -> Optional[str]:
    c = _conn()
    try:
        cur = c.execute("""
            SELECT selector FROM selector_memory
            WHERE tenant_id=? AND url_pattern=? AND semantic=?
        """, (tenant_id, url_pattern, semantic))
        row = cur.fetchone()
        return row[0] if row else None
    finally:
        c.close()


def all_for_tenant(tenant_id: str) -> list[dict]:
    c = _conn()
    try:
        cur = c.execute("""
            SELECT url_pattern, semantic, selector, attempt_count,
                   success_count, last_success_ts, last_failure_ts, updated_at
            FROM selector_memory
            WHERE tenant_id=?
            ORDER BY updated_at DESC
        """, (tenant_id,))
        return [
            {"url_pattern": r[0], "semantic": r[1], "selector": r[2],
             "attempt_count": r[3], "success_count": r[4],
             "last_success_ts": r[5], "last_failure_ts": r[6],
             "updated_at": r[7]}
            for r in cur.fetchall()
        ]
    finally:
        c.close()
