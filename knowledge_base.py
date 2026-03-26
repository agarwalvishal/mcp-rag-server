import logging
import os
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import yaml
from sentence_transformers import SentenceTransformer
from qdrant_client import models, QdrantClient
from qdrant_client.http.exceptions import UnexpectedResponse

logger = logging.getLogger(__name__)

# Namespace UUID for generating deterministic point IDs from content
NAMESPACE = uuid.UUID("a4e2e5e0-6b8a-4f3d-9c1e-2d7f8a9b0c3e")


# ── Configuration ────────────────────────────────────────────────────────────


@dataclass
class Config:
    data_dir: str = "./data"
    file_types: list[str] = field(default_factory=lambda: ["md", "txt", "pdf"])
    qdrant_url: str = "http://localhost:6333"
    qdrant_mode: str = "local"  # "local" (file-based), "memory" (in-memory), "server" (remote)
    qdrant_local_path: str = "./qdrant_data"
    collection_name: str = "knowledge_base"
    embedding_model: str = "nomic-ai/nomic-embed-text-v1.5"
    chunk_size: int = 500
    chunk_overlap: int = 50
    search_top_k: int = 3
    search_score_threshold: float = 0.66
    log_level: str = "INFO"

    @classmethod
    def load(cls, path: str = "config.yaml") -> "Config":
        """Load config from YAML file, then overlay MCPRAG_* env vars."""
        data = {}
        config_path = Path(path)
        if config_path.exists():
            with open(config_path, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            logger.info("Loaded config from %s", path)

        config = cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})

        # Overlay with MCPRAG_* environment variables
        for field_name in cls.__dataclass_fields__:
            env_key = f"MCPRAG_{field_name.upper()}"
            env_val = os.environ.get(env_key)
            if env_val is not None:
                current = getattr(config, field_name)
                if isinstance(current, int):
                    env_val = int(env_val)
                elif isinstance(current, float):
                    env_val = float(env_val)
                elif isinstance(current, list):
                    env_val = [s.strip() for s in env_val.split(",")]
                setattr(config, field_name, env_val)

        return config


# ── Document Loaders ─────────────────────────────────────────────────────────

LOADERS: dict[str, Callable[[Path], str]] = {}


def _load_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


LOADERS[".md"] = _load_text
LOADERS[".txt"] = _load_text

try:
    import pymupdf4llm

    def _load_pdf(path: Path) -> str:
        pages = pymupdf4llm.to_markdown(str(path), page_chunks=True)
        parts = []
        for i, page in enumerate(pages, 1):
            text = page["text"].strip()
            if text:
                parts.append(f"[Page {i}]\n{text}")
        return "\n\n".join(parts)

    LOADERS[".pdf"] = _load_pdf
    logger.debug("PDF loader registered (pymupdf4llm available)")
except ImportError:
    logger.debug("pymupdf4llm not installed — PDF files will be skipped")


# ── Document Loading ─────────────────────────────────────────────────────────


def load_documents(data_dir: str = "data", file_types: list[str] | None = None) -> list[dict]:
    """
    Load documents from the data directory using registered loaders.
    Returns a list of dicts: [{"title": "filename-stem", "content": "...", "source_path": "..."}]
    """
    if file_types is None:
        file_types = ["md", "txt", "pdf"]

    data_path = Path(data_dir)
    if not data_path.exists():
        logger.warning("Data directory '%s' not found.", data_dir)
        return []

    documents = []
    for ext in sorted(file_types):
        dot_ext = f".{ext}"
        if dot_ext not in LOADERS:
            logger.warning("No loader for .%s files — skipping.", ext)
            continue
        loader = LOADERS[dot_ext]
        for filepath in sorted(data_path.glob(f"*.{ext}")):
            try:
                content = loader(filepath).strip()
                if content:
                    rel_path = str(filepath.relative_to(data_path))
                    documents.append({
                        "title": filepath.stem,
                        "content": content,
                        "source_path": rel_path,
                    })
                    logger.debug("Loaded %s", rel_path)
            except Exception:
                logger.exception("Failed to load %s", filepath)

    logger.info("Loaded %d documents from '%s'.", len(documents), data_dir)
    return documents


# ── Chunking ─────────────────────────────────────────────────────────────────


def chunk_document(doc: dict, chunk_size: int = 500, overlap: int = 50) -> list[dict]:
    """
    Split a document into overlapping chunks by paragraph boundaries.
    Extracts section headings from markdown # headers and page numbers from [Page N] markers.
    """
    text = doc["content"]
    title = doc["title"]
    source_path = doc.get("source_path", title)
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks = []

    current_chunk = ""
    current_section: str | None = None
    current_page: int | None = None

    # Track section/page at the start of each chunk
    chunk_section: str | None = None
    chunk_page: int | None = None

    for para in paragraphs:
        # Track markdown headings
        heading_match = re.match(r"^(#{1,6})\s+(.+)$", para.split("\n")[0])
        if heading_match:
            current_section = heading_match.group(2).strip()

        # Track PDF page markers
        page_match = re.match(r"^\[Page (\d+)\]", para)
        if page_match:
            current_page = int(page_match.group(1))

        if len(current_chunk) + len(para) + 2 > chunk_size and current_chunk:
            chunks.append({
                "title": title,
                "content": current_chunk.strip(),
                "section": chunk_section,
                "page": chunk_page,
                "source_path": source_path,
            })
            # Keep trailing overlap for context continuity
            current_chunk = current_chunk[-overlap:] + "\n\n" + para
            chunk_section = current_section
            chunk_page = current_page
        else:
            if not current_chunk:
                chunk_section = current_section
                chunk_page = current_page
            current_chunk = current_chunk + "\n\n" + para if current_chunk else para

    if current_chunk.strip():
        chunks.append({
            "title": title,
            "content": current_chunk.strip(),
            "section": chunk_section,
            "page": chunk_page,
            "source_path": source_path,
        })

    return chunks


# ── Knowledge Base ───────────────────────────────────────────────────────────


class KnowledgeBase:
    """
    Semantic search engine backed by Qdrant and sentence-transformers.
    Loads documents, embeds them, and provides search functionality.
    """

    def __init__(self, config: Config | None = None, **kwargs):
        """
        Initialize with a Config object, or with keyword arguments for backwards compatibility.
        Supported kwargs: qdrant_url, collection_name, model_name.
        """
        if config is not None:
            self.config = config
        else:
            self.config = Config(
                qdrant_url=kwargs.get("qdrant_url", Config.qdrant_url),
                collection_name=kwargs.get("collection_name", Config.collection_name),
                embedding_model=kwargs.get("model_name", Config.embedding_model),
            )

        self.collection_name = self.config.collection_name

        logger.info("Loading embedding model...")
        self.model = SentenceTransformer(self.config.embedding_model, trust_remote_code=True)
        self.vector_dim = self.model.get_sentence_embedding_dimension()
        logger.info("Embedding model loaded. Vector dimension: %d", self.vector_dim)

        if self.config.qdrant_mode == "memory":
            self.client = QdrantClient(":memory:")
            logger.info("Using in-memory Qdrant (no persistence).")
        elif self.config.qdrant_mode == "local":
            try:
                self.client = QdrantClient(path=self.config.qdrant_local_path)
            except RuntimeError as exc:
                if "already accessed" in str(exc):
                    raise RuntimeError(
                        f"Another process is using {self.config.qdrant_local_path}. "
                        "Stop the other instance first, or use --in-memory to run "
                        "without persistence, or use --qdrant-mode server for "
                        "multi-client access."
                    ) from exc
                raise
            logger.info("Using local Qdrant storage at %s.", self.config.qdrant_local_path)
        else:
            self.client = QdrantClient(url=self.config.qdrant_url)
            logger.info("Connected to Qdrant server at %s.", self.config.qdrant_url)

    def _make_point_id(self, content: str) -> str:
        """Generate a deterministic UUID from content to avoid duplicates on re-ingestion."""
        return str(uuid.uuid5(NAMESPACE, content))

    def _ensure_collection(self):
        """Create the Qdrant collection if it doesn't already exist."""
        try:
            self.client.get_collection(self.collection_name)
            logger.info("Collection '%s' already exists.", self.collection_name)
        except (UnexpectedResponse, Exception) as exc:
            if isinstance(exc, UnexpectedResponse) and exc.status_code != 404:
                raise
            logger.info("Creating collection '%s'...", self.collection_name)
            self.client.create_collection(
                collection_name=self.collection_name,
                vectors_config=models.VectorParams(
                    size=self.vector_dim,
                    distance=models.Distance.COSINE,
                ),
            )

    def _embed_and_upsert(self, chunks: list[dict]):
        """Embed chunks and upsert them into Qdrant."""
        if not chunks:
            return 0

        # Nomic model requires "search_document:" prefix for documents
        texts = [f"search_document: {chunk['content']}" for chunk in chunks]
        embeddings = self.model.encode(texts, show_progress_bar=True)

        points = [
            models.PointStruct(
                id=self._make_point_id(chunk["content"]),
                vector=embedding.tolist(),
                payload={
                    "title": chunk["title"],
                    "content": chunk["content"],
                    "section": chunk.get("section"),
                    "page": chunk.get("page"),
                    "source_path": chunk.get("source_path", chunk["title"]),
                },
            )
            for chunk, embedding in zip(chunks, embeddings)
        ]

        batch_size = 64
        for i in range(0, len(points), batch_size):
            self.client.upsert(
                collection_name=self.collection_name,
                points=points[i : i + batch_size],
                wait=True,
            )

        return len(points)

    def setup_collection(self, documents: list[dict]):
        """
        Recreate the Qdrant collection and ingest document chunks.

        The collection is rebuilt from scratch each time to ensure edits,
        renames, and deletions in the data directory are reflected cleanly
        with no stale vectors.
        """
        logger.info("Rebuilding collection '%s'...", self.collection_name)
        if self.client.collection_exists(self.collection_name):
            self.client.delete_collection(self.collection_name)
        self.client.create_collection(
            collection_name=self.collection_name,
            vectors_config=models.VectorParams(
                size=self.vector_dim,
                distance=models.Distance.COSINE,
            ),
        )

        all_chunks = []
        for doc in documents:
            all_chunks.extend(chunk_document(
                doc,
                chunk_size=self.config.chunk_size,
                overlap=self.config.chunk_overlap,
            ))

        if not all_chunks:
            logger.info("No documents to ingest.")
            return

        logger.info("Embedding and ingesting %d chunks...", len(all_chunks))
        count = self._embed_and_upsert(all_chunks)
        logger.info("Ingested %d chunks from %d documents.", count, len(documents))

    def ingest_file(self, path: Path, title: str = "") -> int:
        """
        Load, chunk, embed, and upsert a single file into the knowledge base.
        Returns the number of chunks indexed.
        """
        path = Path(path)
        if not path.is_file():
            raise FileNotFoundError(f"File not found: {path}")

        ext = path.suffix.lower()
        if ext not in LOADERS:
            raise ValueError(f"Unsupported file type: {ext}")

        content = LOADERS[ext](path).strip()
        if not content:
            raise ValueError(f"File is empty: {path}")

        doc = {
            "title": title or path.stem,
            "content": content,
            "source_path": path.name,
        }
        chunks = chunk_document(
            doc,
            chunk_size=self.config.chunk_size,
            overlap=self.config.chunk_overlap,
        )

        self._ensure_collection()
        count = self._embed_and_upsert(chunks)
        logger.info("Ingested '%s': %d chunks indexed.", doc["title"], count)
        return count

    def search(self, query: str, top_k: int | None = None) -> str:
        """
        Embed the query and return the most relevant document chunks.
        """
        query = query.strip()
        if not query:
            return "Error: Search query cannot be empty."

        if top_k is None:
            top_k = self.config.search_top_k

        # Nomic model requires "search_query:" prefix for queries
        query_embedding = self.model.encode(f"search_query: {query}")

        results = self.client.query_points(
            collection_name=self.collection_name,
            query=query_embedding.tolist(),
            limit=top_k,
            score_threshold=self.config.search_score_threshold,
        )

        if not results.points:
            return "I couldn't find a relevant answer in my knowledge base."

        formatted = []
        for point in results.points:
            source = point.payload.get("title", "Unknown")
            content = point.payload["content"]
            score = point.score

            # Build metadata header
            parts = [f"Source: {source}"]
            section = point.payload.get("section")
            if section:
                parts.append(f"Section: {section}")
            page = point.payload.get("page")
            if page is not None:
                parts.append(f"Page: {page}")
            parts.append(f"Relevance: {score:.2f}")

            header = " | ".join(parts)
            formatted.append(f"[{header}]\n{content}")

        citation_hint = (
            "\n\n---\n\n"
            "When using these results, cite each source naturally in your response. "
            "Example: \"The rate limit is 100 req/min (api-reference, p. 3).\""
        )
        return "\n\n---\n\n".join(formatted) + citation_hint
