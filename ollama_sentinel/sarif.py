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


def _fingerprint(file_path: str, category: str, excerpt: str,
                 description: str) -> str:
    """Stable across line drift: hash path+category+excerpt (never lines)."""
    basis = excerpt.strip() or description.strip()
    raw = f"{file_path}\x00{category}\x00{basis}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def build_sarif(
    located_findings: Iterable[Tuple[dict, Relocation]],
    *,
    tool_version: str,
    corroborated_ids: FrozenSet[int] = frozenset(),
) -> dict:
    """Build a SARIF 2.1.0 document from (finding, Relocation) pairs.

    Callers must filter out stale relocations first — every pair here is
    emitted as a result.
    """
    rules_by_id: dict = {}
    results: List[dict] = []
    for finding, reloc in located_findings:
        category = str(finding.get("category") or "general")
        severity = str(finding.get("severity") or "").lower()
        level = _SEVERITY_TO_LEVEL.get(severity, "warning")
        rule_id = f"ollama-sentinel/{category}"
        if rule_id not in rules_by_id:
            rules_by_id[rule_id] = {
                "id": rule_id,
                "name": category,
                "shortDescription": {
                    "text": f"Ollama Sentinel {category} finding"
                },
                "defaultConfiguration": {"level": level},
            }

        file_path = str(finding.get("file_path") or "")
        excerpt = finding.get("verbatim_excerpt") or ""
        region: dict = {"startLine": reloc.start_line, "endLine": reloc.end_line}
        if excerpt.strip():
            region["snippet"] = {"text": excerpt}

        results.append({
            "ruleId": rule_id,
            "level": level,
            "message": {"text": str(finding.get("description") or "")},
            "locations": [{
                "physicalLocation": {
                    "artifactLocation": {"uri": file_path, "uriBaseId": "SRCROOT"},
                    "region": region,
                }
            }],
            "partialFingerprints": {
                "ollamaSentinel/v1": _fingerprint(
                    file_path, category, excerpt,
                    str(finding.get("description") or ""),
                )
            },
            "properties": {
                "severity": severity,
                "occurrence_count": int(finding.get("occurrence_count") or 1),
                "corroborated": finding.get("id") in corroborated_ids,
                "relocation": reloc.status,
            },
        })

    return {
        "$schema": SARIF_SCHEMA,
        "version": SARIF_VERSION,
        "runs": [{
            "tool": {
                "driver": {
                    "name": "ollama-sentinel",
                    "version": tool_version,
                    "informationUri": INFORMATION_URI,
                    "rules": list(rules_by_id.values()),
                }
            },
            "results": results,
        }],
    }
