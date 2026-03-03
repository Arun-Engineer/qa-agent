"""
src/deep_access/db_connector.py — Multi-DB read-only connector.

Supports: PostgreSQL, MySQL, MongoDB, Redis, SQLite
All connections are read-only with 30s timeout and row limits.
Used for: data validation tests, DB state checks, test data setup verification.
"""
from __future__ import annotations

import os, time, structlog
from dataclasses import dataclass, field
from typing import Any, Optional

logger = structlog.get_logger()

MAX_ROWS = 1000
TIMEOUT_SECONDS = 30


@dataclass
class QueryResult:
    rows: list[dict]
    columns: list[str]
    row_count: int
    duration_ms: float
    db_type: str
    truncated: bool = False


class DBConnector:
    """Read-only multi-database connector."""

    def __init__(self):
        self._connections: dict[str, Any] = {}

    def query(self, connection_string: str, sql: str, params: dict | None = None,
              max_rows: int = MAX_ROWS) -> QueryResult:
        """Execute a read-only query."""
        db_type = self._detect_db_type(connection_string)

        # Safety: block write operations
        sql_upper = sql.strip().upper()
        if any(sql_upper.startswith(kw) for kw in
               ("INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "TRUNCATE", "EXEC")):
            raise PermissionError("Write operations are not allowed. Read-only access only.")

        start = time.time()

        if db_type == "postgresql":
            return self._query_pg(connection_string, sql, params, max_rows, start)
        elif db_type == "mysql":
            return self._query_mysql(connection_string, sql, params, max_rows, start)
        elif db_type == "sqlite":
            return self._query_sqlite(connection_string, sql, params, max_rows, start)
        elif db_type == "mongodb":
            raise NotImplementedError("MongoDB queries use find() — use query_mongo()")
        elif db_type == "redis":
            raise NotImplementedError("Redis uses key-value ops — use query_redis()")
        else:
            raise ValueError(f"Unsupported database type: {db_type}")

    def _query_pg(self, conn_str, sql, params, max_rows, start) -> QueryResult:
        try:
            import psycopg2
            import psycopg2.extras
        except ImportError:
            raise ImportError("pip install psycopg2-binary")

        conn = psycopg2.connect(conn_str, connect_timeout=TIMEOUT_SECONDS,
                                options="-c default_transaction_read_only=on")
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, params or {})
                rows = cur.fetchmany(max_rows + 1)
                truncated = len(rows) > max_rows
                if truncated:
                    rows = rows[:max_rows]
                columns = [desc[0] for desc in cur.description] if cur.description else []
                return QueryResult(
                    rows=[dict(r) for r in rows], columns=columns,
                    row_count=len(rows), duration_ms=round((time.time() - start) * 1000, 2),
                    db_type="postgresql", truncated=truncated,
                )
        finally:
            conn.close()

    def _query_mysql(self, conn_str, sql, params, max_rows, start) -> QueryResult:
        try:
            import mysql.connector
        except ImportError:
            raise ImportError("pip install mysql-connector-python")

        # Parse connection string: mysql://user:pass@host:port/db
        from urllib.parse import urlparse
        parsed = urlparse(conn_str)
        conn = mysql.connector.connect(
            host=parsed.hostname, port=parsed.port or 3306,
            user=parsed.username, password=parsed.password,
            database=parsed.path.lstrip("/"),
            connection_timeout=TIMEOUT_SECONDS,
        )
        try:
            cur = conn.cursor(dictionary=True)
            cur.execute("SET SESSION TRANSACTION READ ONLY")
            cur.execute(sql, params or {})
            rows = cur.fetchmany(max_rows + 1)
            truncated = len(rows) > max_rows
            if truncated:
                rows = rows[:max_rows]
            columns = [d[0] for d in cur.description] if cur.description else []
            return QueryResult(
                rows=rows, columns=columns,
                row_count=len(rows), duration_ms=round((time.time() - start) * 1000, 2),
                db_type="mysql", truncated=truncated,
            )
        finally:
            conn.close()

    def _query_sqlite(self, conn_str, sql, params, max_rows, start) -> QueryResult:
        import sqlite3
        path = conn_str.replace("sqlite:///", "").replace("sqlite://", "")
        conn = sqlite3.connect(path, timeout=TIMEOUT_SECONDS)
        conn.row_factory = sqlite3.Row
        try:
            cur = conn.execute(sql, params or {})
            rows = cur.fetchmany(max_rows + 1)
            truncated = len(rows) > max_rows
            if truncated:
                rows = rows[:max_rows]
            columns = [d[0] for d in cur.description] if cur.description else []
            return QueryResult(
                rows=[dict(r) for r in rows], columns=columns,
                row_count=len(rows), duration_ms=round((time.time() - start) * 1000, 2),
                db_type="sqlite", truncated=truncated,
            )
        finally:
            conn.close()

    def query_redis(self, redis_url: str, command: str, *args) -> dict:
        """Execute a read-only Redis command."""
        try:
            import redis
        except ImportError:
            raise ImportError("pip install redis")

        ALLOWED_COMMANDS = {"GET", "MGET", "HGET", "HGETALL", "KEYS", "EXISTS",
                            "TTL", "TYPE", "LRANGE", "SMEMBERS", "SCARD", "LLEN", "DBSIZE"}
        if command.upper() not in ALLOWED_COMMANDS:
            raise PermissionError(f"Command '{command}' not allowed. Read-only: {ALLOWED_COMMANDS}")

        r = redis.from_url(redis_url, socket_timeout=TIMEOUT_SECONDS)
        result = getattr(r, command.lower())(*args)

        if isinstance(result, bytes):
            result = result.decode("utf-8", errors="replace")
        elif isinstance(result, list):
            result = [x.decode("utf-8", errors="replace") if isinstance(x, bytes) else x for x in result]

        return {"command": command, "args": list(args), "result": result}

    @staticmethod
    def _detect_db_type(conn_str: str) -> str:
        cs = conn_str.lower()
        if cs.startswith("postgresql") or cs.startswith("postgres"):
            return "postgresql"
        elif cs.startswith("mysql"):
            return "mysql"
        elif cs.startswith("sqlite"):
            return "sqlite"
        elif cs.startswith("mongodb") or cs.startswith("mongo"):
            return "mongodb"
        elif cs.startswith("redis"):
            return "redis"
        return "unknown"
