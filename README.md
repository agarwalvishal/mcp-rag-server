# mcp-rag-server

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)

Give any LLM — cloud or local — a private knowledge base and web search.
Drop your documents in a folder, start the server, and your AI assistant
instantly becomes an expert on your data.

## What It Does

This MCP server exposes three tools to any connected AI client:

- **`knowledge_base_search`** — semantic search over your local documents (markdown, text, PDF). Finds relevant content even when queries use different words than the source.
- **`web_search`** — live web search via Firecrawl. Especially valuable for local LLMs (Ollama, etc.) that have no built-in web access.
- **`ingest_document`** — add new documents at runtime without restarting the server.

**No separate ingest command.** Documents in `data/` are automatically loaded, chunked, embedded, and indexed on startup.

## Use Cases

**Team Project Documentation** — ADRs, API specs, runbooks, onboarding guides. Connect to Claude Desktop or Cursor and ask "What was the decision on auth middleware?" or "What are the API rate limits?" instead of searching through files. The included sample docs demonstrate this with a fictional startup's technical documentation.

**Research & Study** — Drop research papers (PDFs) and notes into `data/`. Ask questions across all of them: "What caching strategy should we use?" or "What was the Q1 uptime?" Results include page numbers for easy reference back to the source.

**Small Company Knowledge Base** — Internal docs (process guides, product specs, compliance docs) searchable by every team member's AI assistant. Too small for enterprise RAG, too doc-heavy for "just search Slack."

**Local LLM Enhancement** — Running Ollama or another local model? Your LLM has zero built-in capabilities — no document access, no web search, no tools. This server gives it all three via MCP.

**Who this isn't for:** Enterprise-scale (10,000+ docs), non-developers (no UI), or highly specialized domains needing custom chunking/embedding (legal, medical).

## Quickstart

```bash
pip install -r requirements.txt
python mcp_server.py
```

That's it. No Docker, no API keys for embeddings, no config needed.

- Loads documents from `data/`, embeds with Nomic v1.5, stores in local Qdrant (file-based, persists across restarts)
- First run downloads the embedding model (~550MB), cached for subsequent runs
- Web search requires a `FIRECRAWL_API_KEY` in `.env` — KB search works without it

To try it out without persistence:

```bash
python mcp_server.py --in-memory
```

## Try It Out

The included `data/` folder contains sample documents from a fictional startup (Acme Corp). After starting the server, try these queries to see the system in action:

| Query | Expected Source | What It Tests |
|-------|----------------|---------------|
| "What authentication method did the team choose?" | adr-auth-middleware.md | Semantic match on ADR document |
| "What are the API rate limits?" | api-reference.md | Precise technical detail retrieval |
| "How do I set up my dev environment?" | onboarding-guide.md | Natural language to structured guide |
| "What caused the February API outage?" | infrastructure-report.pdf | PDF search with page citation |
| "What are the Q2 infrastructure priorities?" | infrastructure-report.pdf, Page 5 | PDF section + page tracking |
| "Redis vs Memcached comparison" | research-notes.txt | Cross-document semantic search |
| "How to cook pasta" | No results | Irrelevant query correctly filtered |

Replace these with your own documents in `data/` — the server picks them up on next restart.

## Connect to Your AI Client

### Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows):

```json
{
  "mcpServers": {
    "rag-knowledge-base": {
      "command": "/absolute/path/to/project/venv/bin/python",
      "args": ["/absolute/path/to/project/mcp_server.py"],
      "env": {
        "FIRECRAWL_API_KEY": "your-key-here"
      }
    }
  }
}
```

> `FIRECRAWL_API_KEY` is only needed for `web_search`. KB search works without it.

> **Recommended:** Add custom instructions to ensure Claude always checks your knowledge base first. In Claude Desktop, go to Settings and add this to your project instructions:
> *"Always use the rag-knowledge-base tools to search my documents before answering from your own knowledge."*

### Cursor / VS Code

Add to `.cursor/mcp.json` in your project:

```json
{
  "mcpServers": {
    "rag-knowledge-base": {
      "command": "/absolute/path/to/project/venv/bin/python",
      "args": ["/absolute/path/to/project/mcp_server.py"]
    }
  }
}
```

> **Recommended:** Add to `.cursorrules` in your project: *"Always use the rag-knowledge-base tools to search my documents before answering from your own knowledge."*

### Claude Code (CLI)

Add to `.mcp.json` in your project root:

```json
{
  "mcpServers": {
    "rag-knowledge-base": {
      "command": "/absolute/path/to/project/venv/bin/python",
      "args": ["/absolute/path/to/project/mcp_server.py"]
    }
  }
}
```

> **Recommended:** Add to your project's `CLAUDE.md`: *"Always use the rag-knowledge-base tools to search my documents before answering from your own knowledge."*

### Local LLMs (Ollama + Continue.dev)

This is where the server really shines. Your local LLM has no built-in tools — no document access, no web search, nothing. This server gives it semantic document search and web search through a single MCP connection.

With [Continue.dev](https://continue.dev/) in VS Code:

```json title="~/.continue/config.json"
{
  "models": [{ "provider": "ollama", "model": "llama3.1" }],
  "mcpServers": [
    {
      "name": "rag-knowledge-base",
      "command": "/absolute/path/to/project/venv/bin/python",
      "args": ["/absolute/path/to/project/mcp_server.py"]
    }
  ]
}
```

Other MCP-compatible local LLM clients (Open WebUI, msty) follow a similar pattern — point them at `mcp_server.py` as a stdio server.

## Storage Modes

| Mode | Docker? | Persistence? | Best for |
|------|---------|-------------|----------|
| `local` (default) | No | Yes (`./qdrant_data/`) | Most users — just works, survives restarts |
| `memory` | No | No | Trying it out, CI/testing |
| `server` | Yes | Yes (Qdrant container) | Teams, multi-client access, large collections |

Switch modes via CLI:

```bash
# Default — local file-based storage, no Docker
python mcp_server.py

# In-memory — no persistence, great for trying it out
python mcp_server.py --in-memory

# Server mode — connect to a running Qdrant instance
docker compose up qdrant -d
python mcp_server.py --qdrant-mode server --qdrant-url http://localhost:6333
```

Or set `qdrant_mode` in `config.yaml`:

```yaml
qdrant_mode: "server"
qdrant_url: "http://localhost:6333"
```

## Adding Documents

**On startup:** Drop `.md`, `.txt`, `.pdf` files in `data/`. They're automatically loaded, chunked, embedded, and indexed. The collection is rebuilt fresh each startup, so edits, renames, and deletions in `data/` are picked up automatically — no stale data accumulates.

**At runtime:** Use the `ingest_document` tool from your AI client:

```
ingest_document(filepath="/path/to/new-doc.pdf", title="API Reference")
```

The document is immediately searchable for the current session. To make it permanent, add the file to `data/` — it will be included in every future startup.

**Adding files to `data/` while the server is running** has no effect until the next restart. The server reads `data/` once at startup. To index a new file mid-session without restarting, use the `ingest_document` tool.

**File type support:**

| Format | Section headings | Page numbers | Notes |
|--------|-----------------|--------------|-------|
| `.md` | Yes (`#` headers) | No | Best for structured documents |
| `.pdf` | Yes (font-based via pymupdf4llm) | Yes | Headings detected from font size hierarchy |
| `.txt` | No | No | Paragraph-based chunking only |

Search results include metadata when available: `[Source: title | Section: heading | Page: 3 | Relevance: 0.82]`

## Configuration

All settings in `config.yaml`. Priority order (highest wins):

1. CLI flags (`--data-dir`, `--qdrant-mode`, `--in-memory`, `--log-level`)
2. Environment variables (`MCPRAG_DATA_DIR`, `MCPRAG_QDRANT_MODE`, etc.)
3. `config.yaml`
4. Defaults

```yaml
data_dir: "./data"                          # Directory containing documents
file_types: [md, txt, pdf]                  # File types to load
qdrant_mode: "local"                        # local, memory, or server
qdrant_local_path: "./qdrant_data"          # Storage path for local mode
qdrant_url: "http://localhost:6333"         # Qdrant server URL (server mode only)
collection_name: "knowledge_base"           # Qdrant collection name
embedding_model: "nomic-ai/nomic-embed-text-v1.5"  # Sentence-transformers model
chunk_size: 500                             # Target chunk size (characters)
chunk_overlap: 50                           # Overlap between chunks
search_top_k: 3                             # Results per search
search_score_threshold: 0.66                # Minimum relevance (0-1)
log_level: "INFO"                           # DEBUG, INFO, WARNING, ERROR
```

Every setting can be overridden via `MCPRAG_` prefix env vars (e.g., `MCPRAG_CHUNK_SIZE=1000`).

## How It Works — Intelligent Tool Routing

The server is designed for intelligent tool routing. Your LLM checks the knowledge base first. If the relevance score is below the threshold (0.66), the response clearly signals "I couldn't find a relevant answer" — the LLM recognizes this and automatically falls back to web search. No hardcoded routing logic; the tool descriptions and server instructions guide the LLM's decisions.

This matters especially for local LLMs: cloud models like Claude have built-in web search, but local Ollama models have nothing. This server gives them both private document access AND web access through a single MCP connection.

**Important:** The server sends instructions via the MCP protocol asking the LLM to always check the knowledge base first. However, MCP instructions are advisory — the client decides whether to follow them. For the most reliable experience, add custom instructions to your AI client (see the "Connect to Your AI Client" section above for recommended instructions per client).

```
User question
    │
    ▼
LLM calls knowledge_base_search
    │
    ├── Relevant results found → LLM answers from KB
    │
    └── "I couldn't find..." → LLM calls web_search → answers from web
```

## Why These Technical Defaults?

**Why Nomic v1.5?** — Asymmetric embedding model with task-type prefixes (`search_document:` vs `search_query:`). Documents and queries are embedded differently for better retrieval. Consistently strong on MTEB benchmarks. Runs locally, no API key needed.

**Why paragraph-boundary chunking?** — Splits on `\n\n` boundaries, not fixed character counts. Preserves semantic coherence. Configurable chunk size and overlap.

**Why UUID5 deterministic IDs?** — Re-ingesting the same document is idempotent. Restart the server 10 times, you get the same chunks, not duplicates.

**Why score threshold 0.66?** — Empirically determined by mapping score distributions across relevant and irrelevant queries against the included sample documents. Relevant queries score 0.66-0.81, irrelevant ones score 0.48-0.67. The threshold sits at the separation point: high enough to filter noise, low enough to catch all relevant results. Gives the LLM a clean "no answer" signal for out-of-scope queries — critical for the KB-to-web fallback routing.

**Why Qdrant?** — Production vector DB that runs embedded (local mode) or as a server. Start small, scale when needed. Same API either way.

## What Makes This Different

- **Zero-ceremony setup** — Most MCP RAG servers require a separate ingest/index command before search works. This server auto-ingests documents from `data/` on startup. No extra steps.
- **No Docker by default** — Runs with local file-based storage out of the box. Docker is optional for scaling up.
- **Dual-tool pattern** — Private KB search + web search in one server. Especially valuable for local LLMs that have no built-in capabilities.
- **No API keys for embeddings** — Runs Nomic v1.5 locally via sentence-transformers. Your data never leaves your machine.
- **Two readable Python files** — Understand the entire system in 15 minutes. Extend it without learning a framework.

## Architecture

```
┌─────────────────┐     stdio      ┌──────────────────┐
│  AI Client      │◄──────────────►│  mcp_server.py   │
│  (Claude,       │                │  ┌──────────────┐ │
│   Cursor,       │                │  │ FastMCP      │ │
│   Ollama, etc.) │                │  │ 3 tools      │ │
└─────────────────┘                │  └──────┬───────┘ │
                                   └─────────┼─────────┘
                                             │
                              ┌──────────────┼──────────────┐
                              │              │              │
                    ┌─────────▼───┐  ┌───────▼────┐  ┌─────▼───────┐
                    │ KB Search   │  │ Web Search │  │ Ingest Doc  │
                    │ knowledge_  │  │ Firecrawl  │  │ knowledge_  │
                    │ base.py     │  │            │  │ base.py     │
                    └─────────┬───┘  └────────────┘  └─────┬───────┘
                              │                            │
                    ┌─────────▼────────────────────────────▼──┐
                    │         Qdrant Vector DB                 │
                    │  local: ./qdrant_data/ (default)        │
                    │  memory: in-process, no persistence     │
                    │  server: http://localhost:6333           │
                    │                                          │
                    │  • COSINE distance                      │
                    │  • UUID5 deterministic IDs (idempotent)  │
                    │  • Nomic v1.5 embeddings (768d)         │
                    └──────────────────────────────────────────┘
```

## Development

```bash
# Run unit tests (no Qdrant or Docker needed — uses in-memory mode)
python -m pytest test_loaders.py -v

# Run search tests (self-contained — uses in-memory Qdrant and test fixtures)
python -m pytest test_search.py -v

# Run all tests
python -m pytest test_loaders.py test_search.py -v

# Debug with MCP Inspector
pnpx @modelcontextprotocol/inspector python3 mcp_server.py
```

## License

[MIT](LICENSE)
