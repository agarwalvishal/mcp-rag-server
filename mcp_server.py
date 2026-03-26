import argparse
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from firecrawl import Firecrawl
from mcp.server.fastmcp import FastMCP

from knowledge_base import Config, KnowledgeBase, load_documents

load_dotenv()
logger = logging.getLogger(__name__)

# Global instances
knowledge_base: KnowledgeBase | None = None
config: Config | None = None
mcp_server = FastMCP(
    "mcp-rag-server",
    instructions=(
        "You have access to a private knowledge base containing the user's documents. "
        "ALWAYS call knowledge_base_search FIRST for ANY question — the knowledge base "
        "contains private, authoritative content that you cannot access any other way. "
        "Do NOT answer from your own knowledge before checking the knowledge base. "
        "Only use web_search or your own knowledge if knowledge_base_search returns no results. "
        "When answering from the knowledge base, ALWAYS cite the source document and "
        "page number (if available) from the result metadata."
    ),
)


@mcp_server.tool()
def knowledge_base_search(query: str, top_k: int | None = None) -> str:
    """
    Search the private knowledge base for information from indexed documents.

    IMPORTANT: ALWAYS call this tool first for ANY question that could be
    answered by the user's documents. Do not rely on your own knowledge —
    the knowledge base contains private, authoritative content that you
    cannot access any other way. If this tool returns "I couldn't find a
    relevant answer", THEN consider using web_search or your own knowledge.

    Args:
        query: The search query to find relevant documents.
        top_k: Optional number of results to return (omit to use the server default).

    Returns:
        Document excerpts prefixed with metadata headers, for example:
        [Source: api-reference | Section: Rate Limits | Page: 3 | Relevance: 0.82]

        When answering, cite sources naturally in your response, e.g.:
        "The rate limit is 100 req/min (api-reference, Section: Rate Limits, Page 3)."
        Returns a "couldn't find" message if no relevant results exist.
    """
    if knowledge_base is None:
        return "Error: Knowledge base is not initialized."

    query = query.strip()
    if not query:
        return "Error: Search query cannot be empty."

    return knowledge_base.search(query, top_k=top_k)


@mcp_server.tool()
def web_search(query: str) -> str:
    """
    Search the web for information not found in the private knowledge base.

    Only use this tool AFTER knowledge_base_search returns no relevant
    results, or when the user explicitly asks for live/current information
    from the internet.

    Args:
        query: The search query to find information on the web.

    Returns:
        Relevant web search results with titles, URLs, and descriptions.
    """
    query = query.strip()
    if not query:
        return "Error: Search query cannot be empty."

    api_key = os.getenv("FIRECRAWL_API_KEY")
    if not api_key:
        return "Error: FIRECRAWL_API_KEY environment variable is not set."

    try:
        firecrawl = Firecrawl(api_key=api_key)
        results = firecrawl.search(query, limit=5)

        if not results.web:
            return "No results found."

        formatted = []
        for item in results.web:
            title = item.title or "No title"
            url = item.url or ""
            description = item.description or "No description"
            formatted.append(f"**{title}**\n{url}\n{description}")

        return "\n\n---\n\n".join(formatted)

    except Exception:
        logger.exception("Web search failed for query: %s", query)
        return "Error: Web search failed. Check server logs for details."


@mcp_server.tool()
def ingest_document(filepath: str, title: str = "") -> str:
    """
    Add a new document to the private knowledge base without restarting.
    Supports .md, .txt, and .pdf files. The document becomes immediately
    searchable via knowledge_base_search.

    Args:
        filepath: Path to the document file to ingest.
        title: Optional title for the document (defaults to the filename).

    Returns:
        A message indicating how many chunks were indexed.
    """
    if knowledge_base is None:
        return "Error: Knowledge base is not initialized."

    path = Path(filepath)
    if not path.is_file():
        return f"Error: File not found: {filepath}"

    try:
        count = knowledge_base.ingest_file(path, title=title)
        return f"Ingested '{title or path.stem}': {count} chunks indexed."
    except ValueError as e:
        return f"Error: {e}"
    except Exception:
        logger.exception("Failed to ingest %s", filepath)
        return "Error: Ingestion failed. Check server logs for details."


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="MCP RAG Server — semantic search over local documents + web search",
    )
    parser.add_argument(
        "--config", default="config.yaml",
        help="Path to config.yaml (default: config.yaml)",
    )
    parser.add_argument(
        "--data-dir",
        help="Override data directory from config",
    )
    parser.add_argument(
        "--qdrant-url",
        help="Override Qdrant URL from config (implies --qdrant-mode server)",
    )
    parser.add_argument(
        "--qdrant-mode",
        choices=["local", "memory", "server"],
        help="Qdrant storage mode: local (file-based, default), memory (no persistence), server (remote)",
    )
    parser.add_argument(
        "--in-memory",
        action="store_true",
        help="Shortcut for --qdrant-mode memory (no Docker, no persistence)",
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Override log level from config",
    )
    return parser.parse_args()


if __name__ == "__main__":
    # Resolve project root from the script location so that relative paths
    # (data_dir, qdrant_local_path, config.yaml) work regardless of the
    # working directory the parent process uses to spawn us (e.g. Claude Desktop).
    PROJECT_ROOT = Path(__file__).resolve().parent
    os.chdir(PROJECT_ROOT)

    args = parse_args()

    config = Config.load(args.config)

    # CLI overrides
    if args.data_dir:
        config.data_dir = args.data_dir
    if args.qdrant_url:
        config.qdrant_url = args.qdrant_url
        if not args.qdrant_mode and not args.in_memory:
            config.qdrant_mode = "server"
    if args.in_memory:
        config.qdrant_mode = "memory"
    elif args.qdrant_mode:
        config.qdrant_mode = args.qdrant_mode
    if args.log_level:
        config.log_level = args.log_level

    logging.basicConfig(
        level=getattr(logging, config.log_level),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if not os.getenv("FIRECRAWL_API_KEY"):
        logger.warning(
            "FIRECRAWL_API_KEY not set — web_search tool will be unavailable. "
            "Set it in .env or as an environment variable to enable web search."
        )

    logger.info("Initializing knowledge base...")
    knowledge_base = KnowledgeBase(config=config)

    documents = load_documents(config.data_dir, config.file_types)
    knowledge_base.setup_collection(documents)

    logger.info("Starting MCP server...")
    mcp_server.run()
