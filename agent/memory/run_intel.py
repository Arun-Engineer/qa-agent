"""agent/memory/run_intel.py — Persistent run intelligence.

For every completed autonomous run, we write:
  * A `runs` row   — high-level outcome (tenant, url, fingerprint, severity)
  * A `findings` row per finding — for diffing against future runs
  * A `model_snapshots` row — serialized ApplicationModel (compressed)
  * A `flake_scores` row per step — rolling EWMA of pass/fail

Next run consults this data to:
  * Show a "drift since last run" banner (model fingerprint changed)
  * Downweight flaky steps' findings ("this has failed 3/10 times on its own")
  * Surface regressions (finding present now, absent in the last healthy run)
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
import zlib
from pathlib import Path
from typing import Any, Optional


_DB = Path(os.getenv("AUTO_RUN_INTEL_DB", "data/logs/run_intel.sqlite"))


def _conn() -> sqlite3.Connection:
    _DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(_DB), timeout=5)
    c.executescript("""
        CREATE TABLE IF NOT EXISTS runs (
            run_id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            url TEXT NOT NULL,
            model_fingerprint TEXT,
            state TEXT,
            max_severity TEXT,
            findings_count INTEGER DEFAULT 0,
            started_at REAL,
            finished_at REAL
        );
        CREATE TABLE IF NOT EXISTS findings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            source TEXT,
            severity TEXT,
            kind TEXT,
            title TEXT,
            url TEXT,
            confidence REAL,
            oracle TEXT,
            evidence_json TEXT
        );
        CREATE TABLE IF NOT EXISTS model_snapshots (
            run_id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            fingerprint TEXT,
            model_blob BLOB,
            created_at REAL
        );
        CREATE TABLE IF NOT EXISTS flake_scores (
            tenant_id TEXT NOT NULL,
            step_key TEXT NOT NULL,
            runs_total INTEGER DEFAULT 0,
            runs_failed INTEGER DEFAULT 0,
            ewma_fail REAL DEFAULT 0,
            updated_at REAL,
            PRIMARY KEY (tenant_id, step_key)
        );
        CREATE INDEX IF NOT EXISTS idx_findings_run ON findings(run_id);
        CREATE INDEX IF NOT EXISTS idx_runs_tenant ON runs(tenant_id, url);
    """)
    return c


def record_run(run_id: str, *, tenant_id: str, url: str,
               model_fingerprint: str, state: str, max_severity: str,
               findings_count: int, started_at: float,
               finished_at: float) -> None:
    c = _conn()
    try:
        c.execute("""
            INSERT OR REPLACE INTO runs
              (run_id, tenant_id, url, model_fingerprint, state,
               max_severity, findings_count, started_at, finished_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (run_id, tenant_id, url, model_fingerprint, state,
              max_severity, findings_count, started_at, finished_at))
        c.commit()
    finally:
        c.close()


def record_findings(run_id: str, findings: list[dict]) -> None:
    if not findings:
        return
    c = _conn()
    try:
        c.executemany("""
            INSERT INTO findings
              (run_id, source, severity, kind, title, url, confidence, oracle, evidence_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [(run_id, f.get("source"), f.get("severity"), f.get("kind"),
               f.get("title"), f.get("url"), f.get("confidence", 1.0),
               f.get("oracle"), json.dumps(f.get("evidence") or {}, default=str))
              for f in findings])
        c.commit()
    finally:
        c.close()


def record_model_snapshot(run_id: str, tenant_id: str, model_dict: dict) -> None:
    blob = zlib.compress(json.dumps(model_dict, default=str).encode("utf-8"))
    c = _conn()
    try:
        c.execute("""
            INSERT OR REPLACE INTO model_snapshots
              (run_id, tenant_id, fingerprint, model_blob, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, (run_id, tenant_id, model_dict.get("fingerprint", ""),
              blob, time.time()))
        c.commit()
    finally:
        c.close()


def load_model_snapshot(run_id: str) -> Optional[dict]:
    c = _conn()
    try:
        cur = c.execute("SELECT model_blob FROM model_snapshots WHERE run_id=?", (run_id,))
        row = cur.fetchone()
        if not row:
            return None
        return json.loads(zlib.decompress(row[0]).decode("utf-8"))
    finally:
        c.close()


def last_healthy_run(tenant_id: str, url: str) -> Optional[dict]:
    """Most recent DONE run with no critical severity, for this tenant+url."""
    c = _conn()
    try:
        cur = c.execute("""
            SELECT run_id, model_fingerprint, finished_at, max_severity
            FROM runs
            WHERE tenant_id=? AND url=? AND state='DONE'
                  AND max_severity NOT IN ('universal', 'confirmed')
            ORDER BY finished_at DESC LIMIT 1
        """, (tenant_id, url))
        row = cur.fetchone()
        if not row:
            return None
        return {"run_id": row[0], "model_fingerprint": row[1],
                "finished_at": row[2], "max_severity": row[3]}
    finally:
        c.close()


def update_flake(tenant_id: str, step_key: str, failed: bool,
                 alpha: float = 0.2) -> float:
    """Update exponentially weighted moving fail-rate for a step key."""
    c = _conn()
    try:
        cur = c.execute("""
            SELECT runs_total, runs_failed, ewma_fail FROM flake_scores
            WHERE tenant_id=? AND step_key=?
        """, (tenant_id, step_key))
        row = cur.fetchone()
        if row is None:
            total, failed_n, ewma = 0, 0, 0.0
        else:
            total, failed_n, ewma = row
        new_total = total + 1
        new_failed = failed_n + (1 if failed else 0)
        new_ewma = alpha * (1.0 if failed else 0.0) + (1 - alpha) * (ewma or 0.0)
        c.execute("""
            INSERT OR REPLACE INTO flake_scores
              (tenant_id, step_key, runs_total, runs_failed, ewma_fail, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (tenant_id, step_key, new_total, new_failed, new_ewma, time.time()))
        c.commit()
        return new_ewma
    finally:
        c.close()


def flake_score(tenant_id: str, step_key: str) -> float:
    c = _conn()
    try:
        cur = c.execute("""
            SELECT ewma_fail FROM flake_scores WHERE tenant_id=? AND step_key=?
        """, (tenant_id, step_key))
        row = cur.fetchone()
        return float(row[0]) if row else 0.0
    finally:
        c.close()
