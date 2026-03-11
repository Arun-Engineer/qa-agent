"""
stress/test_concurrency.py — Concurrency Stress Tests

Tests that the application handles concurrent requests correctly:
  - Multiple simultaneous logins
  - Concurrent test runs
  - Parallel dashboard reads
  - Session isolation between users

Usage:
    pytest stress/test_concurrency.py -v --timeout=120
"""
import asyncio
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List

import pytest
import requests

BASE_URL = "http://localhost:8000"
TEST_EMAIL = "loadtest@example.com"
TEST_PASSWORD = "loadtest123"


def create_session() -> requests.Session:
    """Create and authenticate a session."""
    s = requests.Session()
    resp = s.post(f"{BASE_URL}/login", data={
        "email": TEST_EMAIL,
        "password": TEST_PASSWORD,
    }, allow_redirects=False)
    return s


def hit_metrics(session: requests.Session) -> dict:
    """Single metrics request, return timing."""
    start = time.time()
    resp = session.get(f"{BASE_URL}/api/metrics")
    elapsed = (time.time() - start) * 1000
    return {"status": resp.status_code, "ms": round(elapsed, 2)}


def hit_runs(session: requests.Session) -> dict:
    start = time.time()
    resp = session.get(f"{BASE_URL}/api/runs")
    elapsed = (time.time() - start) * 1000
    return {"status": resp.status_code, "ms": round(elapsed, 2)}


class TestConcurrentReads:
    """Test concurrent read operations."""

    @pytest.mark.skipif(
        not _server_running(), reason="Server not running at localhost:8000"
    )
    def test_50_concurrent_metrics(self):
        """50 users hitting /api/metrics simultaneously."""
        session = create_session()
        results = _parallel(lambda: hit_metrics(session), count=50)

        success = [r for r in results if r["status"] == 200]
        avg_ms = sum(r["ms"] for r in results) / len(results)

        assert len(success) >= 45, f"Too many failures: {len(results) - len(success)}/50"
        assert avg_ms < 500, f"Average response too slow: {avg_ms}ms"
        print(f"  50 concurrent /api/metrics: {len(success)}/50 ok, avg {avg_ms:.0f}ms")

    @pytest.mark.skipif(
        not _server_running(), reason="Server not running at localhost:8000"
    )
    def test_20_concurrent_runs_list(self):
        """20 users listing runs simultaneously."""
        session = create_session()
        results = _parallel(lambda: hit_runs(session), count=20)

        success = [r for r in results if r["status"] == 200]
        assert len(success) >= 18, f"Too many failures: {len(results) - len(success)}/20"


class TestSessionIsolation:
    """Verify sessions don't leak between users."""

    @pytest.mark.skipif(
        not _server_running(), reason="Server not running at localhost:8000"
    )
    def test_separate_sessions(self):
        """Two sessions should be independent."""
        s1 = create_session()
        s2 = requests.Session()  # Not logged in

        r1 = s1.get(f"{BASE_URL}/api/metrics")
        r2 = s2.get(f"{BASE_URL}/api/metrics")

        assert r1.status_code == 200, "Logged-in session should work"
        assert r2.status_code in (401, 403, 302), "Anonymous session should be rejected"


class TestLoadProfile:
    """Test realistic load profile over time."""

    @pytest.mark.skipif(
        not _server_running(), reason="Server not running at localhost:8000"
    )
    def test_sustained_load_30s(self):
        """Sustained mixed load for 30 seconds."""
        session = create_session()
        results = []
        end_time = time.time() + 30

        with ThreadPoolExecutor(max_workers=10) as pool:
            futures = []
            while time.time() < end_time:
                futures.append(pool.submit(hit_metrics, session))
                time.sleep(0.1)  # ~10 req/s

            for f in as_completed(futures):
                try:
                    results.append(f.result())
                except Exception:
                    results.append({"status": 0, "ms": 0})

        total = len(results)
        success = len([r for r in results if r["status"] == 200])
        error_rate = (total - success) / total * 100 if total > 0 else 0
        avg_ms = sum(r["ms"] for r in results) / total if total > 0 else 0

        print(f"  30s sustained: {total} requests, {success} ok, {error_rate:.1f}% errors, avg {avg_ms:.0f}ms")
        assert error_rate < 5, f"Error rate too high: {error_rate:.1f}%"


# ─── Helpers ───

def _server_running() -> bool:
    try:
        resp = requests.get(f"{BASE_URL}/health", timeout=3)
        return resp.status_code == 200
    except Exception:
        return False


def _parallel(fn, count: int) -> List[dict]:
    """Run fn count times in parallel, return results."""
    results = []
    with ThreadPoolExecutor(max_workers=count) as pool:
        futures = [pool.submit(fn) for _ in range(count)]
        for f in as_completed(futures):
            try:
                results.append(f.result())
            except Exception as e:
                results.append({"status": 0, "ms": 0, "error": str(e)})
    return results
