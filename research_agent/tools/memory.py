# research_agent/tools/memory.py
from __future__ import annotations
import datetime
import uuid
import os
import logging
import json
from typing import List, Dict, Any, Optional, Union, Set
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
    """Simplified memory store implementation (stub)."""

    def __init__(
        self,
        openai_api_key: Optional[str] = None,
        db_path: str = "./.weaviate",
        cache: Optional[Cache] = None
    ):
        self.openai_api_key = openai_api_key
        self.db_path = db_path
        self.cache = cache or Cache()
        self._seen_urls: Set[str] = set()
        
        # In-memory stores for different types
        self._webpages: Dict[str, WebPage] = {}
        self._queries: Dict[str, SearchQuery] = {}
        self._citations: Dict[str, Citation] = {}
        
        logger.info("Using simplified in-memory storage backend")

    def add_webpage(self, webpage: WebPage) -> str:
        """Add or update a webpage in the memory store."""
        try:
            # Generate ID
            if webpage.url:
                page_id = str(uuid.uuid5(uuid.NAMESPACE_URL, webpage.url))
                # Add to seen URLs
                self._seen_urls.add(webpage.url)
            else:
                page_id = str(uuid.uuid4())
                
            # Store the webpage
            self._webpages[page_id] = webpage
            return page_id
        except Exception as e:
            logger.error("Error adding webpage: %s", e)
            return ""

    def add_search_query(self, query: SearchQuery) -> str:
        """Add a search query to the memory store."""
        try:
            query_id = str(uuid.uuid4())
            self._queries[query_id] = query
            return query_id
        except Exception as e:
            logger.error("Error adding search query: %s", e)
            return ""

    def add_citation(self, citation: Citation) -> str:
        """Add a citation to the memory store."""
        try:
            citation_id = str(uuid.uuid4())
            self._citations[citation_id] = citation
            return citation_id
        except Exception as e:
            logger.error("Error adding citation: %s", e)
            return ""

    def has_seen_url(self, url: str) -> bool:
        """Check if URL has been seen before."""
        return url in self._seen_urls

    def find_similar_webpages(self, text: str, limit: int = 5) -> List[WebPage]:
        """Find semantically similar webpages to the given text."""
        # Simple implementation that returns the most recent pages
        try:
            pages = list(self._webpages.values())
            # Sort by creation date (most recent first)
            pages.sort(key=lambda x: x.created_at, reverse=True)
            return pages[:limit]
        except Exception as e:
            logger.error("Error finding similar webpages: %s", e)
            return []

    def find_similar_queries(self, text: str, limit: int = 3) -> List[SearchQuery]:
        """Find semantically similar previous queries."""
        # Simple implementation that returns the most recent queries
        try:
            queries = list(self._queries.values())
            # Sort by creation date (most recent first)
            queries.sort(key=lambda x: x.created_at, reverse=True)
            return queries[:limit]
        except Exception as e:
            logger.error("Error finding similar queries: %s", e)
            return []

    def get_webpage_by_url(self, url: str) -> Optional[WebPage]:
        """Get a webpage by URL."""
        try:
            page_id = str(uuid.uuid5(uuid.NAMESPACE_URL, url))
            return self._webpages.get(page_id)
        except Exception as e:
            logger.error("Error getting webpage: %s", e)
            return None