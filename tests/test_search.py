"""
Tests for knowledge base search relevance, score boundaries, and tool routing.

Self-contained — uses in-memory Qdrant and tests/fixtures/ documents.
No Docker, no pre-ingestion, no external dependencies.

Run with:  ./venv/bin/python -m pytest tests/test_search.py -v
"""

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from knowledge_base import Config, KnowledgeBase, chunk_document, load_documents


FIXTURES = Path(__file__).parent / "fixtures"
NO_ANSWER = "I couldn't find a relevant answer in my knowledge base."


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def kb():
    """Self-contained KnowledgeBase: in-memory Qdrant, loaded from test fixtures."""
    config = Config(qdrant_mode="memory", collection_name="test_search")
    try:
        kb = KnowledgeBase(config=config)
    except Exception:
        pytest.skip("Could not initialize KnowledgeBase (model download may be needed)")
    docs = load_documents(str(FIXTURES), file_types=["md", "txt", "pdf"])
    kb.setup_collection(docs)
    return kb


# ── Document Loading Tests ────────────────────────────────────────────────────

class TestDocumentLoading:
    def test_load_fixtures(self):
        docs = load_documents(str(FIXTURES), file_types=["md", "txt", "pdf"])
        assert len(docs) >= 6
        titles = [d["title"] for d in docs]
        assert "adr-auth-middleware" in titles
        assert "api-reference" in titles
        assert "infrastructure-report" in titles

    def test_load_documents_missing_dir_returns_empty(self):
        docs = load_documents("nonexistent_directory")
        assert docs == []

    def test_documents_have_content(self):
        docs = load_documents(str(FIXTURES), file_types=["md", "txt", "pdf"])
        for doc in docs:
            assert len(doc["content"]) > 50, f"{doc['title']} has too little content"

    def test_documents_have_source_path(self):
        docs = load_documents(str(FIXTURES), file_types=["md", "txt", "pdf"])
        for doc in docs:
            assert "source_path" in doc

    def test_chunk_document_produces_chunks(self):
        doc = {"title": "test", "content": "Para one.\n\nPara two.\n\nPara three."}
        chunks = chunk_document(doc, chunk_size=20, overlap=5)
        assert len(chunks) >= 2
        assert all(c["title"] == "test" for c in chunks)

    def test_chunk_document_has_metadata_fields(self):
        doc = {"title": "test", "content": "Hello world.\n\nMore text.", "source_path": "test.md"}
        chunks = chunk_document(doc)
        for chunk in chunks:
            assert "section" in chunk
            assert "page" in chunk
            assert "source_path" in chunk


# ── Input Validation Tests ────────────────────────────────────────────────────

class TestInputValidation:
    def test_empty_query_returns_error(self, kb):
        result = kb.search("")
        assert "empty" in result.lower()

    def test_whitespace_query_returns_error(self, kb):
        result = kb.search("   ")
        assert "empty" in result.lower()


# ── Score Boundary Tests ─────────────────────────────────────────────────────

class TestScoreBoundaries:
    """Validate score ranges and threshold behavior using raw Qdrant scores."""

    def _raw_score(self, kb, query: str) -> float:
        """Get the top-1 raw cosine similarity score for a query."""
        embedding = kb.model.encode(f"search_query: {query}").tolist()
        results = kb.client.query_points(
            collection_name=kb.collection_name,
            query=embedding,
            limit=1,
            score_threshold=0.0,
        )
        return results.points[0].score if results.points else 0.0

    @pytest.mark.parametrize("query,min_score", [
        ("What authentication method did the team choose for the API?", 0.65),
        ("What are the API rate limits for third-party clients?", 0.65),
        ("How do I set up my development environment at Acme?", 0.65),
        ("Redis vs Memcached caching performance comparison", 0.65),
        ("What caused the February API gateway outage?", 0.65),
        ("What are the Q2 infrastructure priorities?", 0.65),
        ("What are the plans for Q2?", 0.65),
        ("What are the goals for the dashboard redesign?", 0.65),
    ])
    def test_relevant_queries_score_above_minimum(self, kb, query, min_score):
        score = self._raw_score(kb, query)
        assert score >= min_score, f"Expected ≥{min_score}, got {score:.4f}"

    @pytest.mark.parametrize("query,max_score", [
        ("how to bake chocolate cake", 0.55),
        ("history of ancient Rome", 0.55),
        ("stock market trading strategies", 0.60),
        ("how to train a neural network", 0.65),
        ("what is the capital of France", 0.55),
        ("How to cook pasta", 0.55),
    ])
    def test_irrelevant_queries_score_below_maximum(self, kb, query, max_score):
        score = self._raw_score(kb, query)
        assert score <= max_score, f"Expected ≤{max_score}, got {score:.4f}"

    def test_threshold_filters_correctly(self, kb):
        """Verify that search() respects the configured score threshold."""
        old_threshold = kb.config.search_score_threshold
        # With a near-zero threshold, everything matches
        kb.config.search_score_threshold = 0.01
        result_permissive = kb.search("random topic")
        kb.config.search_score_threshold = old_threshold
        assert result_permissive != NO_ANSWER

        # With a very high threshold, nothing matches
        kb.config.search_score_threshold = 0.99
        result_strict = kb.search("What are the API rate limits?")
        kb.config.search_score_threshold = old_threshold
        assert result_strict == NO_ANSWER


# ── Relevant Queries (should return results) ─────────────────────────────────

class TestRelevantQueries:
    """Queries whose answers exist in the fixture documents. Uses natural language
    that requires semantic matching, not keyword copying."""

    @pytest.mark.parametrize("query,expected_source", [
        ("What authentication strategy did the team decide on?", "adr-auth-middleware"),
        ("How are JWT tokens structured?", "adr-auth-middleware"),
        ("What are the API rate limits?", "api-reference"),
        ("How do I set up my development environment at Acme?", "onboarding-guide"),
        ("What are the goals for the dashboard redesign?", "product-requirements"),
        ("Redis vs Memcached performance comparison", "research-notes"),
        ("What was the system uptime in Q1?", "infrastructure-report"),
        ("What caused the February API gateway outage?", "infrastructure-report"),
    ])
    def test_semantic_match(self, kb, query, expected_source):
        result = kb.search(query)
        assert result != NO_ANSWER, f"Expected results for: {query}"
        assert f"Source: {expected_source}" in result

    def test_results_include_relevance_score(self, kb):
        result = kb.search("What authentication method did the team choose?")
        assert "Relevance:" in result

    def test_results_include_citation_hint(self, kb):
        result = kb.search("What authentication method did the team choose?")
        assert "cite each source" in result.lower()

    def test_pdf_results_include_section(self, kb):
        """PDF chunks should have section headings (via pymupdf4llm)."""
        result = kb.search("What are the Q2 infrastructure priorities?")
        assert result != NO_ANSWER
        assert "Section:" in result

    def test_pdf_results_include_page(self, kb):
        """PDF chunks should have page numbers."""
        result = kb.search("What was the system uptime in Q1?")
        assert result != NO_ANSWER
        assert "Page:" in result


# ── Irrelevant Queries (should be filtered out) ──────────────────────────────

class TestIrrelevantQueries:
    """Queries completely outside the fixture document domain."""

    @pytest.mark.parametrize("query", [
        "How to cook pasta",
        "latest iPhone release date",
        "what is the capital of France",
        "how to sort a list in JavaScript",
        "how to bake chocolate cake",
        "compare AWS vs GCP pricing tiers",
    ])
    def test_out_of_scope(self, kb, query):
        result = kb.search(query)
        assert result == NO_ANSWER, f"Expected NO_ANSWER for: {query}"


# ── Re-Ingestion Tests ───────────────────────────────────────────────────────

class TestReIngestion:
    """Verify that setup_collection rebuilds the collection from scratch."""

    def test_reingestion_removes_stale_chunks(self):
        config = Config(qdrant_mode="memory", collection_name="test_reingestion")
        try:
            kb = KnowledgeBase(config=config)
        except Exception:
            pytest.skip("Could not initialize KnowledgeBase")

        original_docs = [
            {"title": "doc1", "content": "The alpaca is a domesticated camelid.", "source_path": "doc1.md"}
        ]
        kb.setup_collection(original_docs)
        assert kb.search("alpaca camelid") != NO_ANSWER

        updated_docs = [
            {"title": "doc1", "content": "Kubernetes orchestrates containerized workloads.", "source_path": "doc1.md"}
        ]
        kb.setup_collection(updated_docs)

        assert kb.search("alpaca camelid") == NO_ANSWER, "Stale content should be gone"
        assert kb.search("Kubernetes container orchestration") != NO_ANSWER

    def test_deleted_file_removed_on_reingestion(self):
        config = Config(qdrant_mode="memory", collection_name="test_deletion")
        try:
            kb = KnowledgeBase(config=config)
        except Exception:
            pytest.skip("Could not initialize KnowledgeBase")

        docs = [
            {"title": "keep", "content": "PostgreSQL is a relational database.", "source_path": "keep.md"},
            {"title": "remove", "content": "Assembly language uses mnemonics for CPU instructions.", "source_path": "remove.md"},
        ]
        kb.setup_collection(docs)
        assert kb.search("assembly mnemonics") != NO_ANSWER

        kb.setup_collection([docs[0]])
        assert kb.search("assembly mnemonics") == NO_ANSWER, "Deleted doc should be gone"
        assert kb.search("PostgreSQL relational database") != NO_ANSWER


# ── Web Search Tool Tests ─────────────────────────────────────────────────────

class TestWebSearch:
    """Tests for the web_search tool's error handling and response format."""

    def test_missing_api_key_returns_error(self, monkeypatch):
        from mcp_server import web_search
        import mcp_server
        monkeypatch.setattr(mcp_server.os, "getenv", lambda key, *a: "" if key == "FIRECRAWL_API_KEY" else os.getenv(key, *a))
        result = web_search("test query")
        assert "FIRECRAWL_API_KEY" in result
        assert "Error" in result

    def test_empty_query_returns_error(self):
        from mcp_server import web_search
        result = web_search("")
        assert "empty" in result.lower()

    @patch("mcp_server.Firecrawl")
    def test_network_error_returns_graceful_message(self, mock_firecrawl_cls, monkeypatch):
        monkeypatch.setenv("FIRECRAWL_API_KEY", "test-key")
        mock_client = MagicMock()
        mock_client.search.side_effect = ConnectionError("Network unreachable")
        mock_firecrawl_cls.return_value = mock_client

        from mcp_server import web_search
        result = web_search("test query")
        assert "Error" in result

    @patch("mcp_server.Firecrawl")
    def test_empty_results(self, mock_firecrawl_cls, monkeypatch):
        monkeypatch.setenv("FIRECRAWL_API_KEY", "test-key")
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.web = []
        mock_client.search.return_value = mock_response
        mock_firecrawl_cls.return_value = mock_client

        from mcp_server import web_search
        result = web_search("obscure query")
        assert "No results found" in result

    @patch("mcp_server.Firecrawl")
    def test_successful_results_formatted(self, mock_firecrawl_cls, monkeypatch):
        monkeypatch.setenv("FIRECRAWL_API_KEY", "test-key")
        mock_client = MagicMock()
        mock_item = MagicMock()
        mock_item.title = "Test Page"
        mock_item.url = "https://example.com"
        mock_item.description = "A test result"
        mock_response = MagicMock()
        mock_response.web = [mock_item]
        mock_client.search.return_value = mock_response
        mock_firecrawl_cls.return_value = mock_client

        from mcp_server import web_search
        result = web_search("test query")
        assert "Test Page" in result
        assert "https://example.com" in result


# ── Tool Routing Tests ────────────────────────────────────────────────────────

class TestToolRouting:
    def test_in_scope_returns_content(self, kb):
        result = kb.search("What authentication method did the team choose?")
        assert result != NO_ANSWER
        assert len(result) > 100

    def test_out_of_scope_returns_no_answer(self, kb):
        result = kb.search("how to sort a list in JavaScript")
        assert result == NO_ANSWER

    def test_no_answer_message_is_informative(self, kb):
        result = kb.search("what is quantum computing")
        assert "couldn't find" in result.lower() or "no relevant" in result.lower()
