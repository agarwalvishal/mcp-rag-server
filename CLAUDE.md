# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

MCP server that exposes three tools: a local semantic search over documents (backed by Qdrant vector DB + sentence-transformers embeddings), a live web search (via Firecrawl SDK), and runtime document ingestion. Supports markdown, plain text, and PDF files. Documents are loaded from the `data/` directory on startup, and can be added at runtime via the `ingest_document` tool.

## Commands

```bash
# Install dependencies (requires Python 3.10+)
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# Run the MCP server (default: local file-based Qdrant, no Docker needed)
python3 mcp_server.py

# Run with in-memory Qdrant (no persistence, good for trying it out)
python3 mcp_server.py --in-memory

# Run with Qdrant server (Docker, for teams/scale)
docker compose up qdrant -d
python3 mcp_server.py --qdrant-mode server

# Run with other options
python3 mcp_server.py --data-dir ./my-docs --qdrant-url http://remote:6333 --log-level DEBUG

# Run unit tests (no Qdrant or Docker needed — uses in-memory mode)
python -m pytest test_loaders.py -v

# Run search tests (self-contained — uses in-memory Qdrant and test fixtures)
python -m pytest test_search.py -v

# Debug/inspect the MCP server with the official inspector
pnpx @modelcontextprotocol/inspector python3 mcp_server.py
```

## Architecture

Two source files, layered as MCP server → KnowledgeBase engine → Qdrant:

- **`mcp_server.py`** — Entry point. Creates a `FastMCP` server ("mcp-rag-server") with server-level `instructions` and registers three tools:
  - `knowledge_base_search` — delegates to `KnowledgeBase.search()` for semantic search against indexed documents. Accepts optional `top_k` parameter.
  - `web_search` — uses the Firecrawl SDK (`firecrawl-py`) for live web search
  - `ingest_document` — indexes a new file at runtime via `KnowledgeBase.ingest_file()`
  - Server `instructions` field tells clients to always check KB first (advisory, not enforced by protocol)
  - On startup (`__main__`): parses CLI args, loads `Config`, initializes `KnowledgeBase`, loads documents, ingests into Qdrant, runs the server via stdio transport

- **`knowledge_base.py`** — Contains `Config` dataclass, document loaders, and the `KnowledgeBase` class:
  - `Config` — loads from `config.yaml`, overlays `MCPRAG_*` env vars. Holds all tunables (chunk size, search params, model name, Qdrant mode, etc.)
  - `qdrant_mode` — `"local"` (file-based, default), `"memory"` (in-memory), or `"server"` (remote Qdrant)
  - Loader registry (`LOADERS`) — maps file extensions to loader functions. `.md`/`.txt` use text loader, `.pdf` uses pymupdf4llm (optional dep, converts PDFs to markdown with font-based heading detection)
  - `load_documents(data_dir, file_types)` reads files using the loader registry
  - `chunk_document(doc)` splits documents into overlapping chunks by paragraph boundaries. Extracts section headings from `#` headers and page numbers from `[Page N]` markers.
  - Embedding model: `nomic-ai/nomic-embed-text-v1.5` via `sentence-transformers` directly
  - Nomic task prefixes: documents use `"search_document: "`, queries use `"search_query: "`
  - Vector DB: Qdrant with COSINE distance, deterministic UUIDs (uuid5) for idempotent re-ingestion
  - `search()` returns results with `[Source: title | Section: heading | Relevance: score]` format
  - `ingest_file(path, title)` loads, chunks, embeds, and upserts a single file at runtime

- **`config.yaml`** — All configuration in one file. Settings can be overridden via `MCPRAG_*` env vars or CLI flags.

- **`data/`** — Documents that form the knowledge base. Supports .md, .txt, and .pdf files.

## Configuration

Settings are loaded in this priority order (highest wins):
1. CLI flags (`--data-dir`, `--qdrant-mode`, `--in-memory`, `--qdrant-url`, `--log-level`)
2. Environment variables (`MCPRAG_DATA_DIR`, `MCPRAG_QDRANT_MODE`, etc.)
3. `config.yaml`
4. Defaults in the `Config` dataclass

## Qdrant Storage Modes

- **`local`** (default) — File-based persistence in `./qdrant_data/`. No Docker needed. Survives restarts.
- **`memory`** — In-memory, no Docker. Data lost on restart. Good for trying it out or CI.
- **`server`** — Connects to Qdrant server at `qdrant_url`. Needs Docker or remote instance. For teams/scale.

## Environment Variables

Requires a `.env` file in the project root (see `.env.example`):
- `FIRECRAWL_API_KEY` — required for the web search tool
- `MCPRAG_*` — optional overrides for any config setting

## Key Search Output Format

Results include metadata: `[Source: title | Section: heading | Relevance: 0.82]`
Section and page are included only when present.
