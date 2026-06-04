"""SARIF 2.1.0 serialization for surfacing findings in editors and CI.

Pure core (relocation + document construction) plus one I/O orchestration
entry added in a later task. Findings store line numbers as of review time;
those drift as files edit, so we re-anchor each finding by its verbatim
excerpt rather than trusting the stored line range.
"""
import hashlib
from dataclasses import dataclass
from typing import FrozenSet, Iterable, List, Tuple

SARIF_SCHEMA = (
    "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/master/"
    "Schemata/sarif-schema-2.1.0.json"
)
SARIF_VERSION = "2.1.0"
INFORMATION_URI = "https://github.com/Skidudeaa/ollama-sentinel"

_SEVERITY_TO_LEVEL = {
    "critical": "error",
    "high": "error",
    "medium": "warning",
    "low": "note",
}


@dataclass
class Relocation:
    """Where a finding currently lives, and how confidently we know it."""
    start_line: int
    end_line: int
    status: str  # "relocated" | "stored" | "stale"


def _normalize_lines(text: str) -> List[str]:
    """Split into lines with leading/trailing whitespace stripped per line."""
    return [ln.strip() for ln in text.splitlines()]


def _strip_blank_edges(lines: List[str]) -> List[str]:
    """Drop blank lines from both ends, keeping internal blanks."""
    start, end = 0, len(lines)
    while start < end and not lines[start]:
        start += 1
    while end > start and not lines[end - 1]:
        end -= 1
    return lines[start:end]


def relocate_finding(content: str, finding: dict) -> Relocation:
    """Re-anchor a finding to its current line by its verbatim excerpt.

    Whitespace is normalized per line so reindentation does not cause false
    staleness. Empty excerpt → cannot relocate, fall back to stored lines
    (status "stored"). Excerpt absent from the file → status "stale".
    """
    stored_start = int(finding.get("line_start") or 1)
    stored_end = int(finding.get("line_end") or stored_start)
    excerpt = (finding.get("verbatim_excerpt") or "").strip()
    if not excerpt:
        return Relocation(stored_start, stored_end, "stored")

    file_lines = _normalize_lines(content)
    excerpt_lines = _strip_blank_edges(_normalize_lines(excerpt))
    if not excerpt_lines:
        return Relocation(stored_start, stored_end, "stored")

    n = len(excerpt_lines)
    matches: List[int] = []  # 1-based start line of each contiguous match
    for i in range(len(file_lines) - n + 1):
        if file_lines[i:i + n] == excerpt_lines:
            matches.append(i + 1)
    if not matches:
        return Relocation(stored_start, stored_end, "stale")

    best = min(matches, key=lambda ln: abs(ln - stored_start))
    return Relocation(best, best + n - 1, "relocated")
