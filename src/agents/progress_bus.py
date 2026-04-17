"""src/agents/progress_bus.py — Thread-safe in-memory pub/sub for run progress.

Used to surface "what is the agent doing right now" from sync workflow code
to an SSE stream, without doubling LLM cost or rewriting the run path.

Lifecycle:
    activate(pid)          # at request start, once per run
    emit("stage", "...")   # called from anywhere on the same thread
    close(pid)             # at request end — sentinel lets SSE disconnect

ThreadPoolExecutor workers (e.g. parallel test-gen batch) must call
`activate(pid)` themselves — threading.local does NOT cross thread
boundaries. A helper `copy_to_worker(pid)` hides the detail.
"""
from __future__ import annotations

import queue
import threading
import time
from typing import Optional

_local = threading.local()
_queues: dict[str, queue.Queue] = {}
_lock = threading.Lock()

SENTINEL = object()
_MAX_QUEUE = 500


def activate(pid: Optional[str]) -> None:
    _local.pid = pid
    if pid:
        with _lock:
            _queues.setdefault(pid, queue.Queue(maxsize=_MAX_QUEUE))


def deactivate() -> None:
    _local.pid = None


def current_id() -> Optional[str]:
    return getattr(_local, "pid", None)


def emit(stage: str, detail: str = "") -> None:
    pid = current_id()
    if not pid:
        return
    q = _queues.get(pid)
    if q is None:
        return
    try:
        q.put_nowait({"stage": stage, "detail": detail, "ts": time.time()})
    except queue.Full:
        pass


def get_queue(pid: str) -> queue.Queue:
    with _lock:
        return _queues.setdefault(pid, queue.Queue(maxsize=_MAX_QUEUE))


def close(pid: str) -> None:
    with _lock:
        q = _queues.pop(pid, None)
    if q is not None:
        try:
            q.put_nowait(SENTINEL)
        except queue.Full:
            pass
