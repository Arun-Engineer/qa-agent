"""agent/memory — Persistent learning across autonomous runs.

Each run produces:
  * Selector updates (Phase 5) — `selector_memory`
  * Run-level intelligence (Phase 6) — `run_intel`

Both persist to local SQLite (free, offline) keyed by tenant.
"""
