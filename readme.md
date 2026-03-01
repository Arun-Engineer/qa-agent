# 🤖 AI QA Agent v5 — Phase 0+1: Foundation

## What Works
- ✅ FastAPI server with full Swagger docs at `/docs`
- ✅ Multi-tenant sessions (SIT/UAT/PROD isolation)
- ✅ Environment-aware access control (PROD = read-only)
- ✅ Test run lifecycle (create, list, status updates)
- ✅ API key authentication
- ✅ Rate limiting (60 req/min)
- ✅ Audit logging (every request tracked)
- ✅ Full test suite (20+ tests, 100% passing)

## Quick Start
```bash
pip install -r requirements.txt
uvicorn src.api.main:app --reload
```
Open http://localhost:8000/docs for interactive API docs.

## Run Tests
```bash
pytest tests/ -v
```

## API Examples
```bash
# Health check (no auth needed)
curl http://localhost:8000/health

# Create SIT session (full access)
curl -X POST http://localhost:8000/api/v1/sessions/ \
  -H "X-API-Key: dev-secret-key-12345" \
  -H "Content-Type: application/json" \
  -d '{"user_id": "arun", "environment": "sit", "task": "test checkout"}'

# Create PROD session (read-only!)
curl -X POST http://localhost:8000/api/v1/sessions/ \
  -H "X-API-Key: dev-secret-key-12345" \
  -H "Content-Type: application/json" \
  -d '{"user_id": "arun", "environment": "prod", "task": "observe homepage"}'

# Validate action in session
curl -X POST "http://localhost:8000/api/v1/sessions/{session_id}/validate-action?action=write" \
  -H "X-API-Key: dev-secret-key-12345"
```

## Next: Phase 2 (Discovery Engine)
Will add: site crawler, page classifier, API mapper — give it a URL and it tests autonomously.
