"""
Utility functions for Ollama Sentinel.
"""
import difflib
import gzip
import logging
import os
import pathlib
import shutil
import tempfile
from typing import List

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
        try:
            abs_path.relative_to(watch_dir_abs)
        except ValueError:
            log.error(f"Security: Path traversal attempt {path} -> {abs_path}")
            return ""
        
        # Read content safely
        return abs_path.read_text(errors="replace")
    except Exception as e:
        log.error(f"Failed to read {path}: {e}")
        return ""


def read_strict(path: pathlib.Path, watch_dir: pathlib.Path) -> str:
    """Read a UTF-8 file with the same containment as :func:`safe_read`, but
    **raise** on any failure instead of degrading to ``""``.

    The read counterpart for the write path: refuses symlinks and path
    traversal (``ValueError``) and decodes with ``errors="strict"``, so a
    non-UTF-8 file raises ``UnicodeDecodeError`` rather than being silently
    corrupted to U+FFFD. That matters because the ``fix`` command writes the
    result back — a lossy decode would persist replacement characters into the
    file's untouched regions.
    """
    if path.is_symlink():
        raise ValueError(f"refusing to read symlink {path}")

    abs_path = path.resolve()
    watch_dir_abs = watch_dir.resolve()
    try:
        abs_path.relative_to(watch_dir_abs)
    except ValueError as e:
        raise ValueError(f"path traversal: {path} -> {abs_path}") from e

    return abs_path.read_text(encoding="utf-8", errors="strict")


def safe_write(path: pathlib.Path, content: str, watch_dir: pathlib.Path) -> None:
    """Atomically write *content* to *path*, contained within *watch_dir*.

    The write counterpart of :func:`safe_read`, but it **raises** rather than
    degrading — a failed or unsafe write must never appear to succeed:

    - rejects symlinks (``ValueError``);
    - enforces ``watch_dir`` containment via ``relative_to`` **before** any
      directory creation (a traversing path is rejected, never created);
    - writes UTF-8 to a temporary file in the same directory, then
      ``os.replace`` (same-filesystem atomic rename), cleaning up the temp on
      failure;
    - preserves the original file's permission mode — ``os.replace`` carries
      the temp inode, whose mode would otherwise leak (a ``0o755`` file would
      silently become ``0o600``);
    - creates missing parent directories within ``watch_dir``.
    """
    if path.is_symlink():
        raise ValueError(f"refusing to write through symlink {path}")

    abs_path = path.resolve()
    watch_dir_abs = watch_dir.resolve()
    try:
        abs_path.relative_to(watch_dir_abs)
    except ValueError as e:
        raise ValueError(f"path traversal: {path} -> {abs_path}") from e

    abs_path.parent.mkdir(parents=True, exist_ok=True)

    existed = abs_path.exists()
    fd, tmp_name = tempfile.mkstemp(
        dir=str(abs_path.parent), prefix=".ollama_sw_", suffix=".tmp"
    )
    tmp_path = pathlib.Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        if existed:
            shutil.copymode(abs_path, tmp_path)
        os.replace(tmp_path, abs_path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


_CHUNK_BY_LINES_WARNED = False


def chunk_content_by_lines(content: str, max_chars: int, overlap: int) -> List[str]:
    """Deprecated: use ollama_sentinel.context.assembler.chunk_by_lines.

    This shim preserves the old char-based API for legacy callers.
    """
    global _CHUNK_BY_LINES_WARNED
    if not _CHUNK_BY_LINES_WARNED:
        log.warning(
            "chunk_content_by_lines is deprecated; use "
            "ollama_sentinel.context.assembler.chunk_by_lines."
        )
        _CHUNK_BY_LINES_WARNED = True

    if len(content) <= max_chars:
        return [content]

    chunks = []
    lines = content.splitlines(True)
    current_chunk = []
    current_size = 0

    for line in lines:
        line_size = len(line)

        if current_size + line_size > max_chars and current_chunk:
            chunks.append("".join(current_chunk))

            overlap_size = 0
            overlap_chunk = []

            for prev_line in reversed(current_chunk):
                if overlap_size + len(prev_line) > overlap:
                    break
                overlap_chunk.insert(0, prev_line)
                overlap_size += len(prev_line)

            current_chunk = overlap_chunk
            current_size = overlap_size

        current_chunk.append(line)
        current_size += line_size

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