# research_agent/tools/browser.py
from __future__ import annotations
import warnings
from bs4 import XMLParsedAsHTMLWarning

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)
import asyncio
import time
import logging
import re
import urllib.parse
from typing import List, Dict, Any, Optional, Tuple
from tenacity import retry, wait_exponential, stop_after_attempt, retry_if_exception_type
from playwright.async_api import async_playwright, Browser, Page, TimeoutError as PlaywrightTimeoutError
from bs4 import BeautifulSoup
import trafilatura
import justext
from markdownify import markdownify
import ipaddress

from research_agent.utils.cache import Cache
from research_agent.core.models import ContentItem
from research_agent.core.logging import get_logger

logger = get_logger(__name__)

class BrowserTool:
    """Enhanced browser tool with multiple extraction methods and caching."""
    
    def __init__(
        self, 
        headless: bool = True,
        user_agent: Optional[str] = None,
        cache: Optional[Cache] = None,
        extraction_methods: List[str] = None,
        max_content_per_page: int = 20000,
        enable_javascript: bool = True,
        page_load_timeout: int = 30
    ):
        self.headless = headless
        self.user_agent = user_agent or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        self.cache = cache or Cache()
        self.extraction_methods = extraction_methods or ["trafilatura", "playwright", "justext"]
        self.max_content_per_page = max_content_per_page
        self.enable_javascript = enable_javascript
        self.page_load_timeout = page_load_timeout
        
        self._playwright = None
        self._browser = None
        self._context = None

    async def _setup_browser(self):
        """Initialize the browser if not already done."""
        if self._browser is None:
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(headless=self.headless)
            self._context = await self._browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent=self.user_agent,
                java_script_enabled=self.enable_javascript
            )

    async def _close(self):
        """Close the browser and Playwright manager."""
        if self._browser:
            await self._browser.close()
            self._browser = None
            self._context = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None
    
    @retry(
        wait=wait_exponential(multiplier=1, min=2, max=10),
        stop=stop_after_attempt(3),
        retry=retry_if_exception_type((PlaywrightTimeoutError, ConnectionError))
    )
    async def _extract_with_playwright(self, page: Page) -> Tuple[str, str]:
        """Extract text content using Playwright's DOM access."""
        # Get the page title
        title = await page.title()
        
        # Extract text content using JavaScript
        content = await page.evaluate("""() => {
            function getVisibleText(node) {
                // Skip if node is null or undefined
                if (!node) {
                    return '';
                }
                
                // Skip invisible elements using safer approach
                if (node.nodeType === Node.ELEMENT_NODE) {
                    try {
                        const style = window.getComputedStyle(node);
                        if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') {
                            return '';
                        }
                    } catch (e) {
                        // Continue if getComputedStyle fails
                    }
                    
                    // Skip common non-content elements
                    const tagName = node.tagName?.toLowerCase();
                    if (tagName && ['script', 'style', 'noscript', 'svg', 'path', 'template'].includes(tagName)) {
                        return '';
                    }
                    
                    // Check for navigation and footer elements
                    if (node.id && ['nav', 'navigation', 'menu', 'footer', 'header'].some(id => node.id.toLowerCase().includes(id))) {
                        return '';
                    }
                    if (node.className && typeof node.className === 'string' && 
                        ['nav', 'navigation', 'menu', 'footer', 'header'].some(cls => node.className.toLowerCase().includes(cls))) {
                        return '';
                    }
                }
                
                // Check if node is a text node
                if (node.nodeType === Node.TEXT_NODE) {
                    return node.textContent?.trim() + ' ' || '';
                }
                
                // Return text from all children
                let text = '';
                if (node.childNodes) {
                    for (const child of node.childNodes) {
                        text += getVisibleText(child);
                    }
                }
                
                // Add extra spacing for block elements
                if (node.nodeType === Node.ELEMENT_NODE) {
                    const tagName = node.tagName?.toLowerCase();
                    if (tagName && ['div', 'p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'section', 'article'].includes(tagName)) {
                        text += '\\n';
                    }
                }
                
                return text;
            }
            
            // First try to find the main content using common selectors
            const mainSelectors = [
                'article', 'main', '[role="main"]', '[itemprop="articleBody"]', 
                '.post-content', '.article-content', '.entry-content', '#content'
            ];
            
            for (const selector of mainSelectors) {
                try {
                    const element = document.querySelector(selector);
                    if (element) {
                        return getVisibleText(element);
                    }
                } catch (e) {
                    // Continue if selector fails
                }
            }
            
            // Fallback to body if no main content element found
            try {
                return getVisibleText(document.body);
            } catch (e) {
                // Last resort, get all text
                return document.body?.innerText || document.documentElement?.innerText || '';
            }
        }""")
        
        return title, content
    
    async def _extract_with_trafilatura(self, url: str, html: str) -> Tuple[Optional[str], Optional[str]]:
        """Extract content using trafilatura library (delegates to utils.extraction)."""
        from research_agent.utils.extraction import extract_with_trafilatura
        return extract_with_trafilatura(html)

    def _extract_with_justext(self, html: str) -> str:
        """Extract main content using justext library (delegates to utils.extraction)."""
        from research_agent.utils.extraction import extract_with_justext
        return extract_with_justext(html)

    def _clean_content(self, content: str) -> str:
        """Clean up extracted content (delegates to utils.extraction)."""
        from research_agent.utils.extraction import clean_text
        return clean_text(content, max_length=self.max_content_per_page)
    
    async def _try_archive(self, url: str) -> Tuple[Optional[str], Optional[str], bool]:
        """Try to fetch content from web archives."""
        try:
            # Wayback Machine
            timestamp = time.strftime("%Y%m%d000000")
            wayback_url = f"https://web.archive.org/web/{timestamp}/{url}"
            
            await self._setup_browser()
            page = await self._context.new_page()
            try:
                await page.goto(wayback_url, timeout=self.page_load_timeout * 1000)
                await page.wait_for_load_state("domcontentloaded")
                title, content = await self._extract_with_playwright(page)
                return title, content, True
            except Exception as e:
                logger.warning(f"Wayback Machine archive failed: {e}")
                
            # Google Cache
            cache_url = f"https://webcache.googleusercontent.com/search?q=cache:{urllib.parse.quote(url)}"
            try:
                await page.goto(cache_url, timeout=self.page_load_timeout * 1000)
                await page.wait_for_load_state("domcontentloaded")
                title, content = await self._extract_with_playwright(page)
                return title, content, True
            except Exception as e:
                logger.warning(f"Google Cache archive failed: {e}")
                
            return None, None, False
        finally:
            if 'page' in locals():
                await page.close()
    
    @staticmethod
    def _validate_url(url: str) -> None:
        """Validate URL to prevent SSRF attacks."""
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ("http", "https"):
            raise ValueError(f"Unsupported URL scheme: {parsed.scheme!r}. Only http/https allowed.")
        hostname = parsed.hostname
        if not hostname:
            raise ValueError(f"URL has no hostname: {url}")
        # Block private/reserved IP ranges
        try:
            addr = ipaddress.ip_address(hostname)
            if addr.is_private or addr.is_loopback or addr.is_reserved or addr.is_link_local:
                raise ValueError(f"URL targets a private/reserved IP address: {hostname}")
        except ValueError as e:
            if "private" in str(e) or "reserved" in str(e) or "loopback" in str(e):
                raise
            # hostname is not an IP literal — that's fine (it's a domain name)

    async def fetch_url(self, url: str) -> ContentItem:
        """Fetch and extract content from a URL with multiple extraction methods."""
        self._validate_url(url)

        # Check cache first
        cache_key = f"content_{url}"
        cached = self.cache.get(cache_key)
        if cached:
            logger.info(f"Cache hit for URL: {url}")
            return cached

        logger.info(f"Fetching content from URL: {url}")
        
        await self._setup_browser()
        page = await self._context.new_page()
        
        result = ContentItem(
            url=url,
            title="",
            content="",
            html="",
            source="browser",
            archived=False,
            timestamp=time.time()
        )
        
        try:
            # Navigate to URL with timeout
            response = await page.goto(url, timeout=self.page_load_timeout * 1000)
            
            # Check if page loaded successfully
            if not response or response.status >= 400:
                logger.warning(f"Failed to load {url}: {response.status if response else 'No response'}")
                title, content, archived = await self._try_archive(url)
                if title or content:
                    result.title = title or "Archived Content"
                    result.content = self._clean_content(content or "")
                    result.archived = True
                    result.source = "archive"
                    self.cache.set(cache_key, result)
                    return result
                raise Exception(f"Failed to load URL: {url}")
                
            # Wait for content to load
            await page.wait_for_load_state("domcontentloaded")
            
            # Get HTML content for extraction
            html = await page.content()
            result.html = html
            
            # Use multiple extraction methods and combine results
            best_content = ""
            best_title = await page.title()
            
            # Try each extraction method in order
            for method in self.extraction_methods:
                if method == "playwright":
                    title, content = await self._extract_with_playwright(page)
                    if content and len(content) > len(best_content):
                        best_content = content
                        if title:
                            best_title = title
                            
                elif method == "trafilatura":
                    title, content = await self._extract_with_trafilatura(url, html)
                    if content and len(content) > len(best_content):
                        best_content = content
                        if title:
                            best_title = title
                            
                elif method == "justext":
                    content = self._extract_with_justext(html)
                    if content and len(content) > len(best_content):
                        best_content = content
            
            # Clean up the content
            result.title = best_title
            result.content = self._clean_content(best_content)
            
            # Cache the result
            self.cache.set(cache_key, result)
            return result
            
        except Exception as e:
            logger.error(f"Error fetching {url}: {str(e)}")
            
            # Try archive sources
            title, content, archived = await self._try_archive(url)
            if title or content:
                result.title = title or "Archived Content"
                result.content = self._clean_content(content or "")
                result.archived = True
                result.source = "archive"
                self.cache.set(cache_key, result)
                return result
                
            # Return empty result if all methods fail
            return result
            
        finally:
            await page.close()
    
    async def fetch_multiple(self, urls: List[str], max_concurrent: int = 3) -> List[ContentItem]:
        """Fetch multiple URLs in parallel with bounded concurrency."""
        semaphore = asyncio.Semaphore(max_concurrent)

        async def _fetch_with_limit(url: str) -> ContentItem:
            async with semaphore:
                return await self.fetch_url(url)

        tasks = [_fetch_with_limit(url) for url in urls]
        return list(await asyncio.gather(*tasks))