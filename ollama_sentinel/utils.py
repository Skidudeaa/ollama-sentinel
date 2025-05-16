"""
Utility functions for Ollama Sentinel.
"""
import difflib
import gzip
import logging
import pathlib
from typing import List, Optional

log = logging.getLogger("ollama-sentinel")


def safe_read(path: pathlib.Path, watch_dir: pathlib.Path) -> str:
    """
    Safely read a file with symlink and path traversal protection.
    
    Args:
        path: Path to read
        watch_dir: Base directory for security checks
        
    Returns:
        File contents as string or empty string on error
    """
    if path.is_symlink():
        log.warning(f"Skipping symlink {path}")
        return ""
    
    try:
        # Resolve to absolute path
        abs_path = path.resolve()
        
        # Check for path traversal
        watch_dir_abs = watch_dir.resolve()
        if not str(abs_path).startswith(str(watch_dir_abs)):
            log.error(f"Security: Path traversal attempt {path} -> {abs_path}")
            return ""
        
        # Read content safely
        return abs_path.read_text(errors="replace")
    except Exception as e:
        log.error(f"Failed to read {path}: {e}")
        return ""


def chunk_content_by_lines(content: str, max_chars: int, overlap: int) -> List[str]:
    """
    Split content into chunks, trying to break at line boundaries.
    
    Args:
        content: Text content to split
        max_chars: Maximum characters per chunk
        overlap: Number of characters to overlap between chunks
        
    Returns:
        List of content chunks
    """
    if len(content) <= max_chars:
        return [content]
    
    chunks = []
    lines = content.splitlines(True)  # Keep line endings
    current_chunk = []
    current_size = 0
    
    for line in lines:
        line_size = len(line)
        
        if current_size + line_size > max_chars and current_chunk:
            # Join current chunk and add to results
            chunks.append("".join(current_chunk))
            
            # Calculate overlap: keep some lines from the end of the current chunk
            overlap_size = 0
            overlap_chunk = []
            
            for line in reversed(current_chunk):
                if overlap_size + len(line) > overlap:
                    break
                overlap_chunk.insert(0, line)
                overlap_size += len(line)
            
            current_chunk = overlap_chunk
            current_size = overlap_size
        
        current_chunk.append(line)
        current_size += line_size
    
    # Add the last chunk if not empty
    if current_chunk:
        chunks.append("".join(current_chunk))
    
    return chunks


def generate_diff(previous: str, current: str, timestamp: str) -> str:
    """
    Generate a unified diff between two strings.
    
    Args:
        previous: Previous content
        current: Current content
        timestamp: Current timestamp for the diff header
        
    Returns:
        Unified diff as a string
    """
    diff = difflib.unified_diff(
        previous.splitlines(),
        current.splitlines(),
        fromfile=f"Previous Review",
        tofile=f"Current Review ({timestamp})",
        lineterm=''
    )
    return "\n".join(diff)


def save_compressed(path: pathlib.Path, content: str) -> None:
    """
    Save content with gzip compression.
    
    Args:
        path: Path to save to
        content: Content to save
    """
    try:
        with gzip.open(path, 'wt', encoding='utf-8') as f:
            f.write(content)
    except Exception as e:
        log.error(f"Failed to save compressed content to {path}: {e}")
        # Fallback to uncompressed
        path = path.with_suffix(path.suffix.replace('.gz', ''))
        path.write_text(content)