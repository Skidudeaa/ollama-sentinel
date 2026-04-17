"""Extract file+line references from tool output (tracebacks, pytest, mypy, ruff).

Pure module: string in, list[Reference] out. Paths are resolved against a
caller-supplied cwd and passed through the same containment check as
safe_read. Non-existent and escaping paths are silently dropped.
"""
from __future__ import annotations

import logging
import pathlib
import re
from dataclasses import dataclass
from typing import List, Optional

log = logging.getLogger("ollama-sentinel")

_MAX_REFERENCES = 50

# Patterns run in declared order; the first that matches a span wins.
# Each pattern captures (path, line) as groups 1 and 2.
_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("traceback", re.compile(r'File "([^"]+)", line (\d+)')),
    ("pytest",    re.compile(r"^([^\s:]+\.py):(\d+)(?::\s|\s+in\s)", re.MULTILINE)),
    ("mypy",      re.compile(r"^([^\s:]+):(\d+):\d+:\s+error:", re.MULTILINE)),
    ("ruff",      re.compile(r"^([^\s:]+):(\d+):\d+:\s+[A-Z]\d+\b", re.MULTILINE)),
    ("generic",   re.compile(r"\b([\w./-]+\.\w{1,5}):(\d+)\b")),
]


@dataclass(frozen=True)
class Reference:
    path: str
    line: Optional[int]
    tool_hint: str


def extract_references(
    text: str, *, cwd: Optional[pathlib.Path] = None
) -> List[Reference]:
    """Return deduped references resolved against cwd, capped at 50."""
    if not text:
        return []

    cwd = (cwd or pathlib.Path.cwd()).resolve()
    seen_spans: set[tuple[int, int]] = set()
    results: list[Reference] = []
    seen_keys: set[tuple[str, int]] = set()
    dropped_traversal = 0
    dropped_missing = 0

    for hint, pattern in _PATTERNS:
        for m in pattern.finditer(text):
            span = m.span()
            if any(
                s_start <= span[0] < s_end or s_start < span[1] <= s_end
                for s_start, s_end in seen_spans
            ):
                continue
            raw_path = m.group(1)
            try:
                line = int(m.group(2))
            except ValueError:
                continue

            candidate = pathlib.Path(raw_path)
            if not candidate.is_absolute():
                candidate = cwd / candidate
            try:
                resolved = candidate.resolve()
                resolved.relative_to(cwd)
            except (OSError, ValueError):
                dropped_traversal += 1
                continue

            if not resolved.is_file():
                dropped_missing += 1
                continue

            key = (str(resolved), line)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            seen_spans.add(span)

            results.append(Reference(path=raw_path, line=line, tool_hint=hint))
            if len(results) >= _MAX_REFERENCES:
                log.debug("extract_references: cap of %s reached", _MAX_REFERENCES)
                _log_drops(dropped_traversal, dropped_missing)
                return results

    _log_drops(dropped_traversal, dropped_missing)
    return results


def _log_drops(traversal: int, missing: int) -> None:
    if traversal:
        log.debug("extract_references: dropped %s path-traversal attempts", traversal)
    if missing:
        log.debug("extract_references: dropped %s references to missing files", missing)
