import pytest
from src.rag.components.hybrid_retriever import BM25Index
from src.rag.services.semantic_cache import SemanticCache
from src.rag.services.query_router import QueryRouter, RouteStrategy
from src.rag.services.query_rewriter import QueryRewriter

class TestBM25Index:
    def test_add_and_search(self):
        idx = BM25Index()
        idx.add_document("doc1", "Login page should validate email format")
        idx.add_document("doc2", "Cart page should allow adding items")
        idx.add_document("doc3", "Profile page should display user email")
        results = idx.search("email validation", top_k=2)
        assert len(results) > 0 and results[0].doc_id == "doc1"

    def test_empty_index(self):
        assert BM25Index().search("anything") == []

    def test_remove_document(self):
        idx = BM25Index()
        idx.add_document("doc1", "test one"); idx.add_document("doc2", "test two")
        idx.remove_document("doc1")
        assert idx.doc_count == 1

class TestSemanticCache:
    def test_store_and_lookup(self):
        cache = SemanticCache(similarity_threshold=0.9)
        emb = [0.1,0.2,0.3,0.4,0.5]
        cache.store("how to test login", emb, "Use Playwright...")
        assert cache.lookup("how to test login", emb) is not None

    def test_cache_miss(self):
        cache = SemanticCache(similarity_threshold=0.99)
        cache.store("q1", [1.0,0.0,0.0], "r1")
        assert cache.lookup("q2", [0.0,1.0,0.0]) is None

    def test_stats(self):
        cache = SemanticCache(); emb = [0.5,0.5]
        cache.store("q1", emb, "r1"); cache.lookup("q1", emb); cache.lookup("q2", [0.0,1.0])
        assert cache.stats["hits"] == 1 and cache.stats["misses"] == 1

    def test_clear(self):
        cache = SemanticCache(); cache.store("q1",[0.1],"r1"); cache.store("q2",[0.2],"r2")
        assert cache.clear() == 2 and cache.stats["size"] == 0

class TestQueryRouter:
    def test_keyword_route(self):
        assert QueryRouter().route("error 500 on POST /api/users TypeError").strategy in (RouteStrategy.BM25, RouteStrategy.HYBRID)
    def test_simple_question(self):
        assert QueryRouter().route("what is BDD?").strategy == RouteStrategy.DIRECT_LLM
    def test_code_route(self):
        assert QueryRouter().route("find the function def test_login_flow in tests").strategy == RouteStrategy.CODE_SEARCH
    def test_empty(self):
        assert QueryRouter().route("").strategy == RouteStrategy.DIRECT_LLM

class TestQueryRewriterPassthrough:
    def test_short(self):
        r = QueryRewriter().rewrite("test login")
        assert r.strategy == "passthrough" and r.rewritten == "test login"
