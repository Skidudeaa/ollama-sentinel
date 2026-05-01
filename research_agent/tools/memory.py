# research_agent/tools/memory.py
from __future__ import annotations
import asyncio
import datetime
import uuid
from typing import List, Dict, Optional, Set
from dataclasses import dataclass, asdict, field

from research_agent.utils.cache import Cache
from research_agent.core.logging import get_logger

logger = get_logger(__name__)

@dataclass
class MemoryItem:
    """Base class for memory items."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: str = field(default_factory=lambda: datetime.datetime.now().isoformat())

@dataclass
class SearchQuery(MemoryItem):
    """Record of a search query."""
    text: Optional[str] = None
    results: List[str] = field(default_factory=list)

@dataclass
class WebPage(MemoryItem):
    """Record of a visited web page."""
    url: Optional[str] = None
    title: Optional[str] = None
    summary: Optional[str] = None
    content: str = ""
    archived: bool = False

@dataclass
class Citation(MemoryItem):
    """Record of a citation from source material."""
    url: Optional[str] = None
    quote: Optional[str] = None
    context: Optional[str] = None

class EnhancedMemoryStore:
    """Persistent memory store backed by Cache (JSON-serialized diskcache).

    Stores webpages, queries, and citations with cache-backed persistence
    so data survives across process restarts. Falls back to in-memory
    when cache operations fail.
    """

    def __init__(
        self,
        openai_api_key: Optional[str] = None,
        db_path: str = "./.weaviate",
        cache: Optional[Cache] = None,
        embedder=None,  # Optional[OllamaEmbedder] — duck-typed to avoid circular import
    ):
        self.openai_api_key = openai_api_key
        self.db_path = db_path
        self.cache = cache or Cache()
        self._embedder = embedder
        self._seen_urls: Set[str] = set()

        # Load persisted state from cache
        self._webpages: Dict[str, WebPage] = {}
        self._queries: Dict[str, SearchQuery] = {}
        self._citations: Dict[str, Citation] = {}
        self._load_from_cache()

        logger.info("Memory store initialized with cache-backed persistence")

    def _load_from_cache(self) -> None:
        """Restore persisted state from cache on startup."""
        try:
            seen = self.cache.get("memory_seen_urls")
            if isinstance(seen, list):
                self._seen_urls = set(seen)

            pages = self.cache.get("memory_webpages")
            if isinstance(pages, dict):
                for pid, data in pages.items():
                    if isinstance(data, dict):
                        self._webpages[pid] = WebPage(**{
                            k: v for k, v in data.items()
                            if k in WebPage.__dataclass_fields__
                        })

            queries = self.cache.get("memory_queries")
            if isinstance(queries, dict):
                for qid, data in queries.items():
                    if isinstance(data, dict):
                        self._queries[qid] = SearchQuery(**{
                            k: v for k, v in data.items()
                            if k in SearchQuery.__dataclass_fields__
                        })
        except Exception as e:
            logger.warning("Failed to restore memory from cache: %s", e)

    def _persist(self) -> None:
        """Persist current state to cache."""
        try:
            self.cache.set("memory_seen_urls", sorted(self._seen_urls))
            self.cache.set(
                "memory_webpages",
                {pid: asdict(wp) for pid, wp in self._webpages.items()},
            )
            self.cache.set(
                "memory_queries",
                {qid: asdict(q) for qid, q in self._queries.items()},
            )
        except Exception as e:
            logger.warning("Failed to persist memory to cache: %s", e)

    def add_webpage(self, webpage: WebPage) -> str:
        """Add or update a webpage in the memory store."""
        try:
            if webpage.url:
                page_id = str(uuid.uuid5(uuid.NAMESPACE_URL, webpage.url))
                self._seen_urls.add(webpage.url)
            else:
                page_id = str(uuid.uuid4())
            self._webpages[page_id] = webpage
            self._persist()
            return page_id
        except Exception as e:
            logger.error("Error adding webpage: %s", e)
            return ""

    def add_search_query(self, query: SearchQuery) -> str:
        """Add a search query to the memory store."""
        try:
            query_id = str(uuid.uuid4())
            self._queries[query_id] = query
            self._persist()
            return query_id
        except Exception as e:
            logger.error("Error adding search query: %s", e)
            return ""

    def add_citation(self, citation: Citation) -> str:
        """Add a citation to the memory store."""
        try:
            citation_id = str(uuid.uuid4())
            self._citations[citation_id] = citation
            self._persist()
            return citation_id
        except Exception as e:
            logger.error("Error adding citation: %s", e)
            return ""

    def has_seen_url(self, url: str) -> bool:
        """Check if URL has been seen before."""
        return url in self._seen_urls

    def find_similar_webpages(self, text: str, limit: int = 5) -> List[WebPage]:
        """Find webpages with keyword overlap to the given text.

        Uses token-overlap scoring rather than pure recency, providing
        basic relevance matching without requiring an embedding model.
        """
        try:
            text_tokens = set(text.lower().split())
            scored = []
            for wp in self._webpages.values():
                wp_text = f"{wp.title or ''} {wp.summary or ''} {wp.url or ''}"
                wp_tokens = set(wp_text.lower().split())
                overlap = len(text_tokens & wp_tokens)
                scored.append((overlap, wp))
            scored.sort(key=lambda x: x[0], reverse=True)
            return [wp for _, wp in scored[:limit] if scored[0][0] > 0]
        except Exception as e:
            logger.error("Error finding similar webpages: %s", e)
            return []

    def find_similar_queries(self, text: str, limit: int = 3) -> List[SearchQuery]:
        """Find previous queries with keyword overlap to the given text."""
        try:
            text_tokens = set(text.lower().split())
            scored = []
            for q in self._queries.values():
                q_tokens = set((q.text or "").lower().split())
                overlap = len(text_tokens & q_tokens)
                scored.append((overlap, q))
            scored.sort(key=lambda x: x[0], reverse=True)
            return [q for _, q in scored[:limit] if scored[0][0] > 0]
        except Exception as e:
            logger.error("Error finding similar queries: %s", e)
            return []

    async def find_similar_webpages_semantic(self, text: str, limit: int = 5) -> List[WebPage]:
        """Rank webpages by cosine similarity. Falls back to token-overlap if no embedder."""
        if self._embedder is None:
            return self.find_similar_webpages(text, limit)
        from ollama_sentinel.context.assembler import ContextItem
        from ollama_sentinel.context.retrievers import SemanticRetriever
        items = [
            ContextItem(text=f"{wp.title or ''} {wp.summary or ''} {wp.url or ''}", embed_key=f"wp:{pid}")
            for pid, wp in self._webpages.items()
        ]
        if not items:
            return []
        ranked = await SemanticRetriever(self._embedder).rank(items, query=text)
        return [
            self._webpages[item.embed_key[3:]]
            for item in ranked[:limit]
            if item.embed_key[3:] in self._webpages
        ]

    async def find_similar_queries_semantic(self, text: str, limit: int = 3) -> List[SearchQuery]:
        """Rank past queries by cosine similarity. Falls back to token-overlap if no embedder."""
        if self._embedder is None:
            return self.find_similar_queries(text, limit)
        from ollama_sentinel.context.assembler import ContextItem
        from ollama_sentinel.context.retrievers import SemanticRetriever
        items = [
            ContextItem(text=q.text or "", embed_key=f"q:{qid}")
            for qid, q in self._queries.items()
            if q.text
        ]
        if not items:
            return []
        ranked = await SemanticRetriever(self._embedder).rank(items, query=text)
        return [
            self._queries[item.embed_key[2:]]
            for item in ranked[:limit]
            if item.embed_key[2:] in self._queries
        ]

    def find_similar_webpages_sync(self, text: str, limit: int = 5) -> List[WebPage]:
        """Sync wrapper: uses semantic ranking if embedder is set, else token-overlap."""
        if self._embedder is None:
            return self.find_similar_webpages(text, limit)
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            return loop.run_until_complete(self.find_similar_webpages_semantic(text, limit))
        finally:
            loop.close()

    def find_similar_queries_sync(self, text: str, limit: int = 3) -> List[SearchQuery]:
        """Sync wrapper: uses semantic ranking if embedder is set, else token-overlap."""
        if self._embedder is None:
            return self.find_similar_queries(text, limit)
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            return loop.run_until_complete(self.find_similar_queries_semantic(text, limit))
        finally:
            loop.close()

    def get_webpage_by_url(self, url: str) -> Optional[WebPage]:
        """Get a webpage by URL."""
        try:
            page_id = str(uuid.uuid5(uuid.NAMESPACE_URL, url))
            return self._webpages.get(page_id)
        except Exception as e:
            logger.error("Error getting webpage: %s", e)
            return None