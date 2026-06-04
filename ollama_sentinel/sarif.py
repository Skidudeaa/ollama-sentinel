"""SARIF 2.1.0 serialization for surfacing findings in editors and CI.

Pure core (relocation + document construction) plus one I/O orchestration
entry added in a later task. Findings store line numbers as of review time;
those drift as files edit, so we re-anchor each finding by its verbatim
excerpt rather than trusting the stored line range.
"""
import hashlib
import json
import logging
import pathlib
from dataclasses import dataclass
from typing import FrozenSet, Iterable, List, Tuple

from .utils import safe_read

log = logging.getLogger("ollama-sentinel")

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

_LEVEL_RANK = {"note": 0, "warning": 1, "error": 2}


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


def _word_linemap(text: str) -> Tuple[List[str], List[int]]:
    """Whitespace-split *text* into words, tracking each word's source line.

    Returns (words, line_per_word) where line_per_word[i] is the 1-based line
    that words[i] came from. Used by the relocation fallback to match a
    newline-flattened excerpt against the file and map back to a line span.
    """
    words: List[str] = []
    line_per_word: List[int] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        for w in line.split():
            words.append(w)
            line_per_word.append(lineno)
    return words, line_per_word


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
    if matches:
        best = min(matches, key=lambda ln: abs(ln - stored_start))
        return Relocation(best, best + n - 1, "relocated")

    # Fallback: the excerpt may have been flattened (newlines collapsed to
    # spaces) by the upstream verbatim-validation gate, so line-block matching
    # misses a genuinely-present multi-line excerpt. Match the excerpt's word
    # sequence against the file's and map the span back to line numbers.
    file_words, file_word_lines = _word_linemap(content)
    excerpt_words = excerpt.split()
    word_matches: List[Tuple[int, int]] = []  # (start_line, end_line) per match
    if excerpt_words:
        m = len(excerpt_words)
        for i in range(len(file_words) - m + 1):
            if file_words[i:i + m] == excerpt_words:
                word_matches.append(
                    (file_word_lines[i], file_word_lines[i + m - 1])
                )
    if not word_matches:
        return Relocation(stored_start, stored_end, "stale")

    best_start, best_end = min(
        word_matches, key=lambda se: abs(se[0] - stored_start)
    )
    return Relocation(best_start, best_end, "relocated")


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
        else:
            existing = rules_by_id[rule_id]["defaultConfiguration"]["level"]
            if _LEVEL_RANK.get(level, 1) > _LEVEL_RANK.get(existing, 1):
                rules_by_id[rule_id]["defaultConfiguration"]["level"] = level

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


@dataclass
class SurfaceSummary:
    """Counts from one SARIF generation pass."""
    emitted: int
    relocated: int
    stale: int
    unverified: int
    path: pathlib.Path


def generate_sarif_file(
    db,
    watch_dir: "pathlib.Path | str",
    output_dir: "pathlib.Path | str",
    *,
    tool_version: str,
    out_path: "pathlib.Path | str | None" = None,
) -> SurfaceSummary:
    """Read open findings, relocate by excerpt, write findings.sarif.

    Read-only with respect to the DB and source: stale findings are excluded
    from the document and counted, never auto-resolved. ``out_path`` overrides
    the default ``output_dir/findings.sarif`` destination.
    """
    watch_dir = pathlib.Path(watch_dir)
    output_dir = pathlib.Path(output_dir)
    rows = db.get_all_unresolved()

    corroborated_ids: set = set()
    paths = sorted({r["file_path"] for r in rows})
    if paths:
        try:
            corroborated_ids = {
                r["id"] for r in db.get_findings_with_incidents(paths)
            }
        except Exception as e:  # corroboration is enrichment; never fatal
            log.warning("Corroboration lookup failed (%s); marking none.", e)

    content_cache: dict = {}

    def _content(rel: str):
        # safe_read returns "" (never raises) for missing/unreadable/symlink/
        # traversal, so check existence explicitly: a gone file is stale, an
        # empty-but-present file goes through relocation like any other.
        if rel not in content_cache:
            abs_path = watch_dir / rel
            content_cache[rel] = (
                safe_read(abs_path, watch_dir) if abs_path.is_file() else None
            )
        return content_cache[rel]

    located: list = []
    relocated = stale = unverified = 0
    for r in rows:
        content = _content(r["file_path"])
        if content is None:
            stale += 1
            continue
        reloc = relocate_finding(content, r)
        if reloc.status == "stale":
            stale += 1
            continue
        if reloc.status == "stored":
            unverified += 1
        else:
            relocated += 1
        located.append((r, reloc))

    doc = build_sarif(
        located, tool_version=tool_version,
        corroborated_ids=frozenset(corroborated_ids),
    )
    sarif_path = pathlib.Path(out_path) if out_path else output_dir / "findings.sarif"
    sarif_path.parent.mkdir(parents=True, exist_ok=True)
    sarif_path.write_text(json.dumps(doc, indent=2))

    return SurfaceSummary(
        emitted=len(located),
        relocated=relocated,
        stale=stale,
        unverified=unverified,
        path=sarif_path,
    )
