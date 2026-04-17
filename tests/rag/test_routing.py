import pytest
from src.rag.services.query_router import QueryRouter, RouteStrategy
from src.rag.agents.adaptive_router import AdaptiveRouter, DEFAULT_SOURCES

class TestQueryRouterEdge:
    def test_jira(self): assert QueryRouter().route("Status of PROJ-1234?").strategy in (RouteStrategy.BM25, RouteStrategy.HYBRID)
    def test_file_ref(self): assert QueryRouter().route("Check auth_controller.py and user_service.ts").strategy in (RouteStrategy.BM25, RouteStrategy.HYBRID)
    def test_long(self): assert QueryRouter().route("Compare authentication flow between mobile and desktop checking expired tokens and refresh rotation").strategy in (RouteStrategy.VECTOR, RouteStrategy.HYBRID)
    def test_error_code(self): assert QueryRouter().route("error 500 status code 502 on production API").strategy == RouteStrategy.BM25
    def test_greeting(self): assert QueryRouter().route("hello, can you help?").strategy == RouteStrategy.DIRECT_LLM
    def test_batch(self):
        r = QueryRouter().route_batch(["what is BDD?","error 404 in login.py","test cart"])
        assert len(r)==3 and r[0].strategy==RouteStrategy.DIRECT_LLM

class TestAdaptiveRouter:
    def test_sources(self): assert len(AdaptiveRouter().sources) == len(DEFAULT_SOURCES)
    def test_feedback(self):
        r = AdaptiveRouter(); r.record_feedback("test",["qa_knowledge_base"],0.8)
        assert len(r._feedback)==1
