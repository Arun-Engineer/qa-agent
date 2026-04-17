"""src/api/routes/agents_stream.py — Server-Sent Events for LangGraph runs.

GET  /api/v1/agents/stream?workflow=<name>&spec=<text>
POST /api/v1/agents/stream   body: {"workflow": "...", "spec": "...", "context": {...}}

Streams one JSON line per graph node update:
    event: node
    data: {"node": "generate", "state": {...}, "ts": "..."}

    event: done
    data: {"ok": true, "final": {...}}

Works for any workflow whose subgraph is exposed via `agent.workflows`.
We call the graph's `.stream()` method in "updates" mode so each node's
partial state diff is emitted as soon as it lands — this is what powers
token-by-token-like UI feedback without needing LangSmith or WebSockets.

Currently supports:
  - langgraph_test_gen     (per-story subgraph; spec must contain stories)
  - langgraph_spec_review  (5-dim parallel graph)
  - langgraph_api_test     (self-healing per-test subgraph)
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Any, AsyncIterator, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel


router = APIRouter()


class StreamRequest(BaseModel):
    workflow: str
    spec: str
    context: Optional[dict[str, Any]] = None


def _pick_graph_and_init(workflow: str, spec: str, ctx: dict) -> tuple[Any, dict, str]:
    """Return (compiled_graph, initial_state, thread_id) for a workflow."""
    run_id = ctx.get("run_id") or datetime.utcnow().strftime("run%Y%m%d%H%M%S")

    if workflow == "langgraph_spec_review":
        from agent.workflows.langgraph_spec_review import _graph
        return _graph(), {"spec": spec}, f"{run_id}:review"

    if workflow == "langgraph_test_gen":
        # For streaming we expose the per-story subgraph keyed on the first story.
        from agent.workflows.langgraph_test_gen import _story_graph
        return _story_graph(), {
            "story_id": ctx.get("story_id", "US-1"),
            "story_title": ctx.get("story_title", "Streamed story"),
            "story_text": spec,
            "cases": [],
            "reflection": {},
            "retries": 0,
            "max_retries": 2,
        }, f"{run_id}:{ctx.get('story_id','US-1')}"

    if workflow == "langgraph_api_test":
        from agent.workflows.langgraph_api_test import _graph
        return _graph(), {
            "spec": spec,
            "step": ctx.get("step") or {"tool": "pytest_runner", "args": {"path": "tests/test_stream.py"}},
            "path": (ctx.get("step") or {}).get("args", {}).get("path", "tests/test_stream.py"),
            "heals": 0,
            "max_heals": 2,
        }, f"{run_id}:api"

    raise HTTPException(status_code=400, detail=f"unsupported streaming workflow: {workflow}")


def _sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


async def _stream_graph(graph: Any, init: dict, thread_id: str) -> AsyncIterator[str]:
    """Run the LangGraph in a worker thread so we can await its output. Each
    node yields an SSE frame. Terminates with a `done` event."""
    yield _sse("start", {"thread_id": thread_id, "ts": datetime.utcnow().isoformat()})

    loop = asyncio.get_event_loop()
    queue: asyncio.Queue = asyncio.Queue()
    sentinel = object()

    def _runner():
        try:
            for update in graph.stream(init, config={"configurable": {"thread_id": thread_id}}, stream_mode="updates"):
                # update is {node_name: partial_state_dict}
                loop.call_soon_threadsafe(queue.put_nowait, ("node", update))
            loop.call_soon_threadsafe(queue.put_nowait, ("done", None))
        except Exception as e:
            loop.call_soon_threadsafe(queue.put_nowait, ("error", str(e)))
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, sentinel)

    asyncio.get_event_loop().run_in_executor(None, _runner)

    final_state: dict[str, Any] = {}
    while True:
        item = await queue.get()
        if item is sentinel:
            break
        kind, payload = item
        if kind == "node":
            for node_name, diff in (payload or {}).items():
                if isinstance(diff, dict):
                    final_state.update(diff)
                yield _sse("node", {
                    "node": node_name,
                    "state": diff,
                    "ts": datetime.utcnow().isoformat(),
                })
        elif kind == "error":
            yield _sse("error", {"message": payload})
        elif kind == "done":
            yield _sse("done", {"ok": True, "final": final_state,
                                "ts": datetime.utcnow().isoformat()})


@router.post("/stream")
async def stream_run(req: StreamRequest, request: Request):
    graph, init, thread_id = _pick_graph_and_init(req.workflow, req.spec, req.context or {})
    return StreamingResponse(
        _stream_graph(graph, init, thread_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/stream")
async def stream_run_get(workflow: str, spec: str, request: Request):
    """Browser-friendly GET variant — EventSource can't POST."""
    graph, init, thread_id = _pick_graph_and_init(workflow, spec, {})
    return StreamingResponse(
        _stream_graph(graph, init, thread_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/progress")
async def progress_stream(id: str):
    """SSE channel keyed by a client-generated progress id (X-Progress-Id).
    The /api/run handler activates the bus for this id; workflow code emits
    stage labels; this endpoint forwards them to the browser. Closes on run
    sentinel or after a long idle window.
    """
    from functools import partial
    from queue import Empty
    from src.agents import progress_bus

    q = progress_bus.get_queue(id)

    async def gen():
        yield _sse("start", {"id": id, "ts": datetime.utcnow().isoformat()})
        loop = asyncio.get_event_loop()
        idle = 0
        while True:
            try:
                item = await loop.run_in_executor(None, partial(q.get, True, 1.0))
            except Empty:
                idle += 1
                # Heartbeat once per 10s so proxies keep the stream open.
                if idle % 10 == 0:
                    yield _sse("heartbeat", {"idle_s": idle})
                # Give up after 5 min of silence — the run is almost certainly gone.
                if idle > 300:
                    yield _sse("done", {"reason": "idle_timeout"})
                    break
                continue
            idle = 0
            if item is progress_bus.SENTINEL:
                yield _sse("done", {"reason": "run_ended"})
                break
            yield _sse("progress", item)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/workflows")
async def list_streamable() -> dict:
    return {
        "workflows": [
            {"name": "langgraph_spec_review",
             "description": "5-dimension parallel spec review, streams each dimension as it finishes."},
            {"name": "langgraph_test_gen",
             "description": "Per-story test case generation with reflection loop; streams each subgraph node."},
            {"name": "langgraph_api_test",
             "description": "Self-healing API test subgraph; streams generate/run/diagnose/regenerate nodes."},
        ]
    }
