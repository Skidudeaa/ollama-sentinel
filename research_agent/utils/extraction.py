# research_agent/utils/extraction.py
from __future__ import annotations
import re
from typing import Optional, Tuple
from html import unescape
from bs4 import BeautifulSoup
import trafilatura
import justext
from markdownify import markdownify as md
from research_agent.core.logging import get_logger

logger = get_logger(__name__)

def extract_with_trafilatura(html: str) -> Tuple[Optional[str], Optional[str]]:
    """Extract content using trafilatura library.
    
    Args:
        html: HTML content
        
    Returns:
        Tuple of (title, content)
    """
    try:
        # Extract with trafilatura (good for news articles)
        extracted = trafilatura.extract(
            html,
            output_format="xml",
            include_comments=False,
            include_tables=True,
            no_fallback=False
        )
        
        if extracted:
            # Parse the XML to get title and content
            soup = BeautifulSoup(extracted, "xml")
            title_elem = soup.find("title")
            title = title_elem.text if title_elem else None
            
            # Convert to markdown for better readability
            content = md(str(soup))
            return title, content
        
        return None, None
    except Exception as e:
        logger.warning(f"Trafilatura extraction failed: {e}")
        return None, None

def extract_with_justext(html: str, language: str = "English") -> str:
    """Extract main content using justext library.
    
    Args:
        html: HTML content
        language: Content language
        
    Returns:
        Extracted text content
    """
    try:
        # JusText is good for non-news articles
        paragraphs = justext.justext(html, justext.get_stoplist(language))
        content = "\n\n".join([p.text for p in paragraphs if not p.is_boilerplate])
        return content
    except Exception as e:
        logger.warning(f"JusText extraction failed: {e}")
        return ""

def extract_with_bs4(html: str) -> Tuple[Optional[str], Optional[str]]:
    """Extract content using BeautifulSoup.
    
    Args:
        html: HTML content
        
    Returns:
        Tuple of (title, content)
    """
    try:
        soup = BeautifulSoup(html, "html.parser")
        
        # Extract title
        title_elem = soup.find("title")
        title = title_elem.text if title_elem else None
        
        # Remove script, style, and other non-content elements
        for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
            tag.decompose()
        
        # Try to find main content area
        content_tags = ["article", "main", "div.content", "div.article", "div.post"]
        main_content = None
        
        for tag in content_tags:
            if "." in tag:
                tag_name, class_name = tag.split(".")
                main_content = soup.find(tag_name, class_=class_name)
            else:
                main_content = soup.find(tag)
                
            if main_content:
                break
        
        # If no main content area found, use body
        if not main_content:
            main_content = soup.body
        
        # If still no content, return empty string
        if not main_content:
            return title, ""
        
        # Extract text
        content = main_content.get_text(separator="\n", strip=True)
        return title, content
        
    except Exception as e:
        logger.warning(f"BeautifulSoup extraction failed: {e}")
        return None, None

def clean_text(text: str, max_length: int = 20000) -> str:
    """Clean extracted text content.
    
    Args:
        text: Raw text content
        max_length: Maximum content length
        
    Returns:
        Cleaned text
    """
    if not text:
        return ""
        
    # Decode HTML entities
    text = unescape(text)
    
    # Remove excessive whitespace
    text = re.sub(r'\s+', ' ', text)
    text = re.sub(r'\n\s*\n', '\n\n', text)
    
    # Remove very short lines that are likely menu items
    lines = text.split('\n')
    filtered_lines = [line for line in lines if len(line.strip()) > 20 or re.search(r'[.!?]', line)]
    text = '\n'.join(filtered_lines)
    
    # Trim to max length
    if len(text) > max_length:
        text = text[:max_length] + "...[content truncated]"
        
    return text