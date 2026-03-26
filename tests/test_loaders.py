"""
Tests for document loaders, configuration, chunk metadata, and runtime ingestion.

Unit tests — no Qdrant or embedding model required (except TestRuntimeIngestion).

Run with:  ./venv/bin/python -m pytest tests/test_loaders.py -v
"""

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from knowledge_base import (
    Config,
    KnowledgeBase,
    LOADERS,
    chunk_document,
    load_documents,
)

FIXTURES = Path(__file__).parent / "fixtures"


# ── Config Tests ──────────────────────────────────────────────────────────────

class TestConfig:
    def test_defaults(self):
        config = Config()
        assert config.data_dir == "./data"
        assert config.qdrant_url == "http://localhost:6333"
        assert config.qdrant_mode == "local"
        assert config.qdrant_local_path == "./qdrant_data"
        assert config.chunk_size == 500
        assert config.search_score_threshold == 0.66

    def test_load_from_yaml(self, tmp_path):
        cfg = {"data_dir": "/tmp/docs", "chunk_size": 1000, "log_level": "DEBUG"}
        yaml_path = tmp_path / "config.yaml"
        yaml_path.write_text(yaml.dump(cfg))
        config = Config.load(str(yaml_path))
        assert config.data_dir == "/tmp/docs"
        assert config.chunk_size == 1000
        assert config.log_level == "DEBUG"

    def test_load_missing_file_uses_defaults(self):
        config = Config.load("nonexistent.yaml")
        assert config.data_dir == "./data"

    def test_env_var_override(self, monkeypatch):
        monkeypatch.setenv("MCPRAG_QDRANT_URL", "http://remote:6333")
        monkeypatch.setenv("MCPRAG_CHUNK_SIZE", "1000")
        config = Config.load("nonexistent.yaml")
        assert config.qdrant_url == "http://remote:6333"
        assert config.chunk_size == 1000

    def test_unknown_yaml_keys_ignored(self, tmp_path):
        cfg = {"data_dir": "/tmp/docs", "unknown_key": "value"}
        yaml_path = tmp_path / "config.yaml"
        yaml_path.write_text(yaml.dump(cfg))
        config = Config.load(str(yaml_path))
        assert config.data_dir == "/tmp/docs"

    def test_qdrant_mode_config(self):
        config = Config(qdrant_mode="memory")
        assert config.qdrant_mode == "memory"

    def test_qdrant_mode_from_yaml(self, tmp_path):
        cfg = {"qdrant_mode": "server", "qdrant_url": "http://remote:6333"}
        yaml_path = tmp_path / "config.yaml"
        yaml_path.write_text(yaml.dump(cfg))
        config = Config.load(str(yaml_path))
        assert config.qdrant_mode == "server"


# ── Text Loader Tests ────────────────────────────────────────────────────────

class TestTextLoader:
    def test_md_loader_registered(self):
        assert ".md" in LOADERS

    def test_txt_loader_registered(self):
        assert ".txt" in LOADERS

    def test_load_md_file(self):
        content = LOADERS[".md"](FIXTURES / "sample.md")
        assert "Sample Document" in content
        assert "First Section" in content

    def test_load_txt_file(self):
        content = LOADERS[".txt"](FIXTURES / "sample.txt")
        assert "plain text document" in content

    def test_load_documents_from_fixtures(self):
        docs = load_documents(str(FIXTURES), file_types=["md", "txt"])
        titles = [d["title"] for d in docs]
        assert "sample" in titles  # sample.md and sample.txt share stem
        assert len(docs) >= 2

    def test_load_documents_has_source_path(self):
        docs = load_documents(str(FIXTURES), file_types=["md"])
        for doc in docs:
            assert "source_path" in doc
            assert doc["source_path"].endswith(".md")


# ── PDF Loader Tests ─────────────────────────────────────────────────────────

class TestPDFLoader:
    @pytest.fixture(autouse=True)
    def _check_pymupdf(self):
        try:
            import pymupdf  # noqa: F401
        except ImportError:
            pytest.skip("pymupdf not installed")

    def test_pdf_loader_registered(self):
        assert ".pdf" in LOADERS

    def test_load_pdf_file(self):
        content = LOADERS[".pdf"](FIXTURES / "sample.pdf")
        assert "[Page 1]" in content
        assert "[Page 2]" in content
        assert "test PDF document" in content

    def test_load_documents_includes_pdf(self):
        docs = load_documents(str(FIXTURES), file_types=["pdf"])
        assert len(docs) >= 1
        titles = [d["title"] for d in docs]
        assert "sample" in titles


# ── Chunk Metadata Tests ─────────────────────────────────────────────────────

class TestChunkMetadata:
    def test_section_from_markdown_headers(self):
        doc = {
            "title": "test",
            "content": "# Intro\n\nFirst paragraph.\n\n## Details\n\nSecond paragraph with more detail.",
            "source_path": "test.md",
        }
        chunks = chunk_document(doc, chunk_size=30, overlap=5)
        # At least one chunk should have a section heading
        sections = [c["section"] for c in chunks if c["section"]]
        assert len(sections) > 0
        assert any("Intro" in s or "Details" in s for s in sections)

    def test_page_from_pdf_markers(self):
        doc = {
            "title": "test-pdf",
            "content": "[Page 1]\nFirst page content.\n\n[Page 2]\nSecond page content.",
            "source_path": "test.pdf",
        }
        chunks = chunk_document(doc, chunk_size=30, overlap=5)
        pages = [c["page"] for c in chunks if c["page"] is not None]
        assert len(pages) > 0
        assert 1 in pages

    def test_source_path_preserved(self):
        doc = {"title": "test", "content": "Hello world.\n\nAnother paragraph.", "source_path": "docs/test.md"}
        chunks = chunk_document(doc)
        for chunk in chunks:
            assert chunk["source_path"] == "docs/test.md"

    def test_chunks_have_all_metadata_keys(self):
        doc = {"title": "test", "content": "Some content here.", "source_path": "test.md"}
        chunks = chunk_document(doc)
        for chunk in chunks:
            assert "title" in chunk
            assert "content" in chunk
            assert "section" in chunk
            assert "page" in chunk
            assert "source_path" in chunk


# ── Runtime Ingestion Tests (uses in-memory Qdrant, no Docker needed) ────────

class TestRuntimeIngestion:
    """These tests use in-memory Qdrant — no Docker required."""

    @pytest.fixture(scope="class")
    def kb(self):
        config = Config(qdrant_mode="memory", collection_name="test_ingestion")
        try:
            kb = KnowledgeBase(config=config)
        except Exception:
            pytest.skip("Could not initialize KnowledgeBase (model download may be needed)")
        yield kb

    def test_ingest_file(self, kb):
        count = kb.ingest_file(FIXTURES / "sample.md")
        assert count > 0

    def test_ingest_file_not_found(self, kb):
        with pytest.raises(FileNotFoundError):
            kb.ingest_file(Path("nonexistent.md"))

    def test_ingest_unsupported_type(self, kb):
        # Create a temp file with unsupported extension
        with tempfile.NamedTemporaryFile(suffix=".xyz", delete=False) as f:
            f.write(b"test")
            f.flush()
            try:
                with pytest.raises(ValueError, match="Unsupported"):
                    kb.ingest_file(Path(f.name))
            finally:
                os.unlink(f.name)

    def test_search_after_ingest(self, kb):
        """Verify that ingested documents are searchable."""
        result = kb.search("Sample Document")
        assert "sample" in result.lower() or "Sample" in result
