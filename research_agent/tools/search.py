# research_agent/tools/search.py
from __future__ import annotations
import asyncio
import logging
import time
from typing import Dict, List, Optional, Any, Literal, Union, Callable
from enum import Enum
from dataclasses import dataclass
import random
from urllib.parse import urlparse
from tenacity import retry, wait_exponential, stop_after_attempt, retry_if_exception_type

from langchain_core.tools import BaseTool
from langchain_community.utilities import SerpAPIWrapper
from duckduckgo_search import DDGS
import requests
from pydantic import BaseModel, Field, ValidationError
from research_agent.utils.cache import Cache
from research_agent.core.logging import get_logger

logger = get_logger(__name__)

@dataclass
class SearchResult:
    url: str
    title: str
    snippet: str
    position: int
    source: str
    domain: str = ""
    
    def __post_init__(self):
        if not self.domain and self.url:
            parsed = urlparse(self.url)
            self.domain = parsed.netloc

class SearchEngine(Enum):
    SERPAPI = "serp"
    DUCKDUCKGO = "ddg"
    AUTO = "auto"

class SearchInput(BaseModel):
    query: str = Field(..., description="Search query to execute")
    engine: Optional[SearchEngine] = Field(None, description="Search engine to use")
    
class SearchTool(BaseTool):
    """Enhanced multi-source search tool with caching and fallbacks."""
    name: str = "search"
    description: str = "Search the web for information on a topic"
    args_schema: type = SearchInput
    
    serpapi_api_key: Optional[str] = Field(None, description="API key for SERPAPI")
    default_engine: SearchEngine = Field(SearchEngine.AUTO, description="Default search engine")
    results_per_query: int = Field(10, description="Number of results per query")
    cache: Optional[Cache] = Field(None, description="Cache for search results")
    serp_wrapper: Optional[Any] = Field(None, description="SerpAPI wrapper")
    ddg: Optional[Any] = Field(None, description="DuckDuckGo client")
    
    def __init__(
        self, 
        serpapi_api_key: Optional[str] = None,
        default_engine: SearchEngine = SearchEngine.AUTO,
        cache: Optional[Cache] = None,
        results_per_query: int = 10
    ):
        super().__init__()
        self.serpapi_api_key = serpapi_api_key
        self.default_engine = default_engine
        self.results_per_query = results_per_query
        self.cache = cache or Cache()
        
        # Initialize search backends
        if self.serpapi_api_key:
            self.serp_wrapper = SerpAPIWrapper(
                serpapi_api_key=self.serpapi_api_key,
                params={
                    "engine": "google",
                    "google_domain": "google.com",
                    "gl": "us",
                    "hl": "en"
                }
            )
        else:
            self.serp_wrapper = None
            if self.default_engine == SearchEngine.SERPAPI:
                logger.warning("SERPAPI key not provided. Falling back to DuckDuckGo.")
                self.default_engine = SearchEngine.DUCKDUCKGO
                
        # Initialize DuckDuckGo
        self.ddg = DDGS()
    
    @retry(
        wait=wait_exponential(multiplier=1, min=2, max=10),
        stop=stop_after_attempt(3),
        retry=retry_if_exception_type((requests.RequestException, ConnectionError))
    )
    def _search_serpapi(self, query: str) -> List[SearchResult]:
        """Execute search with SerpAPI."""
        cache_key = f"serpapi_{query}"
        
        # Check cache first
        cached = self.cache.get(cache_key)
        if cached:
            logger.info(f"Cache hit for query: {query}")
            return cached
        
        results = []
        try:
            if not self.serp_wrapper:
                return []
                
            logger.info(f"Executing SerpAPI search for: {query}")
            raw = self.serp_wrapper.results(query)
            
            # Process organic results
            if "organic_results" in raw:
                for i, res in enumerate(raw["organic_results"]):
                    results.append(SearchResult(
                        url=res.get("link", ""),
                        title=res.get("title", ""),
                        snippet=res.get("snippet", ""),
                        position=i,
                        source="serpapi"
                    ))
                    
            # Add knowledge graph if available
            if "knowledge_graph" in raw:
                kg = raw["knowledge_graph"]
                if "description" in kg:
                    results.append(SearchResult(
                        url=kg.get("source", {}).get("link", ""),
                        title=kg.get("title", "Knowledge Graph Result"),
                        snippet=kg.get("description", ""),
                        position=-1,  # High priority
                        source="serpapi_kg"
                    ))
                    
            # Add related questions if available
            if "related_questions" in raw:
                for i, q in enumerate(raw["related_questions"]):
                    results.append(SearchResult(
                        url=q.get("link", ""),
                        title=q.get("question", "Related Question"),
                        snippet=q.get("snippet", ""),
                        position=100 + i,  # Low priority
                        source="serpapi_related"
                    ))
                    
            # Cache the results
            self.cache.set(cache_key, results)
            return results
            
        except Exception as e:
            logger.error(f"SerpAPI search error: {e}")
            return []
    
    @retry(
        wait=wait_exponential(multiplier=1, min=2, max=10),
        stop=stop_after_attempt(3),
        retry=retry_if_exception_type((requests.RequestException, ConnectionError))
    )
    def _search_ddg(self, query: str) -> List[SearchResult]:
        """Execute search with DuckDuckGo."""
        cache_key = f"ddg_{query}"
        
        # Check cache first
        cached = self.cache.get(cache_key)
        if cached:
            logger.info(f"Cache hit for query: {query}")
            return cached
            
        results = []
        try:
            logger.info(f"Executing DuckDuckGo search for: {query}")
            raw_results = list(self.ddg.text(query, max_results=self.results_per_query))
            
            for i, res in enumerate(raw_results):
                results.append(SearchResult(
                    url=res.get("href", ""),
                    title=res.get("title", ""),
                    snippet=res.get("body", ""),
                    position=i,
                    source="ddg"
                ))
                
            # Add news results if available
            try:
                news_results = list(self.ddg.news(query, max_results=3))
                for i, res in enumerate(news_results):
                    results.append(SearchResult(
                        url=res.get("url", ""),
                        title=res.get("title", ""),
                        snippet=res.get("body", ""),
                        position=50 + i,  # Medium priority
                        source="ddg_news"
                    ))
            except Exception:
                # News search can fail without affecting the main results
                pass
                
            # Cache the results
            self.cache.set(cache_key, results)
            return results
            
        except Exception as e:
            logger.error(f"DuckDuckGo search error: {e}")
            return []
    
    def _search_multi(self, query: str, engine: Optional[SearchEngine] = None) -> List[SearchResult]:
        """Execute search across multiple engines with fallbacks."""
        engine = engine or self.default_engine
        
        # Handle AUTO strategy
        if engine == SearchEngine.AUTO:
            # Prefer SerpAPI if available, otherwise DuckDuckGo
            engine = SearchEngine.SERPAPI if self.serp_wrapper else SearchEngine.DUCKDUCKGO
            
        # Primary search
        if engine == SearchEngine.SERPAPI:
            results = self._search_serpapi(query)
            # Fallback to DuckDuckGo if SerpAPI fails or returns no results
            if not results:
                logger.info("SerpAPI returned no results, falling back to DuckDuckGo")
                results = self._search_ddg(query)
        else:
            results = self._search_ddg(query)
            # No need to fallback to SerpAPI since DuckDuckGo rarely fails completely
            
        # Deduplicate by URL
        seen_urls = set()
        unique_results = []
        
        for res in results:
            if res.url and res.url not in seen_urls:
                seen_urls.add(res.url)
                unique_results.append(res)
                
        return unique_results[:self.results_per_query]
    
    def _run(self, query: str, engine: Optional[SearchEngine] = None) -> List[SearchResult]:
        """Run the search and return results."""
        if isinstance(engine, str):
            try:
                engine = SearchEngine(engine)
            except ValueError:
                engine = None
                
        return self._search_multi(query, engine)
        
    async def _arun(self, query: str, engine: Optional[SearchEngine] = None) -> List[SearchResult]:
        """Async version that runs in an executor."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._run, query, engine)