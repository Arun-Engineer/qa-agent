"""
src/api/routes/memory.py — Phase 4 Memory & Deep Access API.

Endpoints:
  GET  /api/v1/memory/stats       → vector store + graph stats
  POST /api/v1/memory/search      → vector similarity search
  POST /api/v1/memory/bugs/search → search known bugs
  POST /api/v1/memory/bugs/register → register a bug from failure
  GET  /api/v1/memory/graph/stats → knowledge graph statistics
  POST /api/v1/memory/graph/blast-radius → impact analysis
  POST /api/v1/memory/db/query    → read-only DB query (admin only)
  GET  /api/v1/memory/logs/search → time-correlated log search
  GET  /api/v1/memory/runs        → list test runs from SQL store
  GET  /api/v1/memory/runs/{id}   → get run details + results
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from typing import Optional
from datetime import datetime

from tenancy.rbac import require_min_tenant_role

router = APIRouter()


class VectorSearchRequest(BaseModel):
    query: str
    collection: str = "qa_knowledge"
    top_k: int = 5


class BugSearchRequest(BaseModel):
    query: str = ""
    status: str = "open"
    limit: int = 20


class BugRegisterRequest(BaseModel):
    test_name: str
    error_message: str
    severity: str = "medium"
    module: str = ""
    traceback: str = ""
    triage_category: str = "BUG"


class DBQueryRequest(BaseModel):
    connection_string: str
    sql: str
    max_rows: int = 100


class BlastRadiusRequest(BaseModel):
    node_id: str
    max_depth: int = 3


def _tenant_id(request: Request) -> str:
    return getattr(request.state, "tenant_id", None) or "default"


@router.get("/stats")
async def memory_stats(request: Request, ctx=Depends(require_min_tenant_role("viewer"))):
    """Overview of memory subsystems."""
    from src.memory.vector_store import VectorStore
    from src.memory.graph_kb import GraphKB
    vs = VectorStore()
    gk = GraphKB()
    return {
        "vector_store": {"count": vs.count()},
        "graph_kb": gk.get_stats(),
    }


@router.post("/search")
async def vector_search(req: VectorSearchRequest, request: Request,
                        ctx=Depends(require_min_tenant_role("member"))):
    from src.memory.vector_store import VectorStore
    vs = VectorStore(collection=req.collection)
    results = vs.search(req.query, top_k=req.top_k)
    return {
        "results": [{"id": r.id, "text": r.text[:300], "score": round(r.score, 4),
                      "metadata": r.metadata} for r in results],
        "count": len(results),
    }


@router.post("/bugs/search")
async def bug_search(req: BugSearchRequest, request: Request,
                     ctx=Depends(require_min_tenant_role("member"))):
    from src.memory.bug_registry import BugRegistry
    br = BugRegistry(tenant_id=_tenant_id(request))
    if req.query:
        results = br.find_similar_bugs(req.query, top_k=req.limit)
        return {"results": results, "source": "vector"}
    else:
        bugs = br.sql.search_bugs(_tenant_id(request), status=req.status, limit=req.limit)
        return {
            "results": [
                {"bug_id": b.id, "title": b.title, "severity": b.severity,
                 "status": b.status, "occurrences": b.occurrence_count,
                 "last_seen": str(b.last_seen)}
                for b in bugs
            ],
            "source": "sql",
        }


@router.post("/bugs/register")
async def register_bug(req: BugRegisterRequest, request: Request,
                       ctx=Depends(require_min_tenant_role("member"))):
    from src.memory.bug_registry import BugRegistry
    br = BugRegistry(tenant_id=_tenant_id(request))
    result = br.register_failure(
        test_name=req.test_name, error_message=req.error_message,
        severity=req.severity, module=req.module,
        traceback=req.traceback, triage_category=req.triage_category,
    )
    return result


@router.get("/graph/stats")
async def graph_stats(request: Request, ctx=Depends(require_min_tenant_role("viewer"))):
    from src.memory.graph_kb import GraphKB
    gk = GraphKB()
    return gk.get_stats()


@router.post("/graph/blast-radius")
async def blast_radius(req: BlastRadiusRequest, request: Request,
                       ctx=Depends(require_min_tenant_role("member"))):
    from src.memory.graph_kb import GraphKB
    gk = GraphKB()
    affected = gk.blast_radius(req.node_id, max_depth=req.max_depth)
    return {"node_id": req.node_id, "affected_nodes": list(affected),
            "count": len(affected)}


@router.post("/db/query")
async def db_query(req: DBQueryRequest, request: Request,
                   ctx=Depends(require_min_tenant_role("admin"))):
    """Read-only database query — Admin/Owner only."""
    from src.deep_access.db_connector import DBConnector
    try:
        connector = DBConnector()
        result = connector.query(req.connection_string, req.sql, max_rows=req.max_rows)
        return {
            "columns": result.columns, "rows": result.rows,
            "row_count": result.row_count, "duration_ms": result.duration_ms,
            "db_type": result.db_type, "truncated": result.truncated,
        }
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/logs/search")
async def log_search(request: Request, q: str = "error", hours: int = 1,
                     source: str = "auto", limit: int = 50,
                     ctx=Depends(require_min_tenant_role("member"))):
    from src.deep_access.log_aggregator import LogAggregator
    agg = LogAggregator()
    end = datetime.utcnow()
    start = end - __import__("datetime").timedelta(hours=hours)
    result = agg.search(q, start_time=start, end_time=end, source=source, max_results=limit)
    return {
        "entries": [
            {"timestamp": str(e.timestamp), "level": e.level,
             "message": e.message[:500], "source": e.source}
            for e in result.entries
        ],
        "total_count": result.total_count,
        "source": result.source,
        "backends": agg.available_backends(),
    }


@router.get("/runs")
async def list_runs(request: Request, limit: int = 50,
                    ctx=Depends(require_min_tenant_role("viewer"))):
    from src.memory.sql_store import SQLStore
    store = SQLStore()
    runs = store.list_runs(_tenant_id(request), limit=limit)
    return [
        {"id": r.id, "status": r.status, "environment": r.environment,
         "total_tests": r.total_tests, "passed": r.passed, "failed": r.failed,
         "duration_ms": r.duration_ms, "created_at": str(r.created_at),
         "provider": r.provider, "model": r.model}
        for r in runs
    ]


@router.get("/runs/{run_id}")
async def get_run(run_id: str, request: Request,
                  ctx=Depends(require_min_tenant_role("viewer"))):
    from src.memory.sql_store import SQLStore
    store = SQLStore()
    run = store.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    results = store.get_results(run_id)
    return {
        "run": {"id": run.id, "status": run.status, "environment": run.environment,
                "total_tests": run.total_tests, "passed": run.passed,
                "failed": run.failed, "duration_ms": run.duration_ms,
                "provider": run.provider, "model": run.model,
                "created_at": str(run.created_at)},
        "results": [
            {"id": r.id, "test_name": r.test_name, "status": r.status,
             "duration_ms": r.duration_ms, "error_message": r.error_message,
             "triage_category": r.triage_category}
            for r in results
        ],
    }
