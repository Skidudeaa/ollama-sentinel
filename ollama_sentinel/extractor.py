"""
Finding extractor — validate or extract Finding records from review output.

Grounded path (default): the schema-constrained Ollama call returns pre-structured
findings; ``validate_findings`` checks each one's ``verbatim_excerpt`` against the
file's cited line range and drops mismatches.

Legacy path (``--no-grounding``): regex-based extraction from free-form review
markdown via ``extract_findings_legacy`` — pattern-matches without consulting the
file, kept only as a debug-comparison escape hatch.
"""
import logging
import re
from typing import List

from .violation_db import Finding

log = logging.getLogger("ollama-sentinel")

_REQUIRED_KEYS = {"line_start", "line_end", "category", "severity", "verbatim_excerpt", "description"}


def _parse_finding(raw: dict, file_path: str) -> Finding | None:
    """Validate a raw dict and return a Finding, or None if malformed."""
    if not isinstance(raw, dict):
        return None
    if not _REQUIRED_KEYS.issubset(raw.keys()):
        return None
    try:
        return Finding(
            file_path=file_path,
            line_start=int(raw["line_start"]),
            line_end=int(raw["line_end"]),
            category=str(raw["category"]),
            severity=str(raw["severity"]),
            description=str(raw["description"]),
            verbatim_excerpt=str(raw.get("verbatim_excerpt", "")),
        )
    except (ValueError, TypeError):
        return None


# Matches a line reference in either the pre-grounding free-form style
# ("line 5", "lines 5-10") or the grounded-prose degrade style the prompt
# asks for ("Line Range: `line_start..line_end`", e.g. "Line Range: `1..2`").
# After the label, markdown punctuation (** ` : =) may sit between the label
# and the first number; the range separator may be ``..``, a hyphen/dash, or
# the word "to".
_LINE_REF_PATTERN = re.compile(
    r"(?:line[\s_]*range|line[\s_]*start|lines?)"
    r"[\s:=*`'\"]*(\d+)"
    r"(?:\s*(?:\.\.|[-–—]|to)\s*[`'\"]*(\d+))?",
    re.IGNORECASE,
)

# Splits review text into per-issue blocks at the start of each markdown
# heading ("### N."), bullet ("- ", "* "), or numbered item ("1. ", "1) ").
# Lookahead keeps the marker with its block. Bold spans like ``**Claim:**``
# are not boundaries: ``[-*]\s`` needs whitespace after the marker, which
# ``**`` does not have.
_ISSUE_BOUNDARY_PATTERN = re.compile(r"\n(?=\s*(?:#{1,6}\s|[-*]\s|\d+[.)]\s))")

# Pulls the claim text out of a grounded-prose block, dropping the
# ``**Claim:**`` scaffolding so the Finding description reads cleanly.
_CLAIM_PATTERN = re.compile(r"\bclaim\b\s*[:*`]*\s*(.+)", re.IGNORECASE | re.DOTALL)

_SEVERITY_KEYWORDS = {
    "critical": "critical",
    "severe": "critical",
    "high": "high",
    "important": "high",
    "medium": "medium",
    "moderate": "medium",
    "low": "low",
    "minor": "low",
    "style": "low",
    "nit": "low",
}

_CATEGORY_KEYWORDS = {
    "bug": "bug",
    "error": "bug",
    "null": "bug",
    "crash": "bug",
    "security": "security",
    "vulnerability": "security",
    "injection": "security",
    "xss": "security",
    "performance": "performance",
    "slow": "performance",
    "memory": "performance",
    "style": "style",
    "naming": "style",
    "readability": "style",
    "design": "design",
    "refactor": "design",
    "architecture": "design",
}


def _validate_verbatim(finding: dict, file_content: str) -> bool:
    """Return True if the finding's verbatim_excerpt is found in the cited lines.

    Slices ``file_content`` at the finding's ``line_start``..``line_end``,
    normalises whitespace (collapse runs to single spaces, strip), and checks
    that the finding's ``verbatim_excerpt`` (also whitespace-normalised) is
    contained in that slice.
    """
    lines = file_content.splitlines()
    try:
        start = max(0, int(finding.get("line_start", 0)) - 1)
        end = int(finding.get("line_end", 0))
    except (ValueError, TypeError):
        return False
    if start >= len(lines):
        return False
    end = min(end, len(lines))
    slice_text = "\n".join(lines[start:end])

    def _normalise(text: str) -> str:
        import re as _re
        return _re.sub(r"\s+", " ", text).strip()

    excerpt = _normalise(finding.get("verbatim_excerpt", ""))
    if not excerpt:
        return False
    return excerpt in _normalise(slice_text)


def extract_findings_legacy(review_text: str, file_path: str) -> List[Finding]:
    """Regex-based extraction from free-form review markdown.

    The pre-grounding extraction path: parses bullet points or numbered items
    that reference line numbers, classifies severity and category by keyword
    match. Pattern-matches without consulting the file under review, so it
    cannot ground claims — but it's the only extraction path that works when
    the schema-constrained output mode is disabled (e.g. via --no-grounding
    for debug comparison). Returns Findings with empty ``verbatim_excerpt``.
    """
    findings: List[Finding] = []
    # One block per issue: heading ("### N."), bullet, or numbered item. The
    # grounded-prose format keeps each issue's Line Range / Excerpt / Claim
    # together under one heading, so splitting on issue boundaries (not on
    # every bullet) is what lets the per-issue Claim become the description.
    blocks = _ISSUE_BOUNDARY_PATTERN.split(review_text)

    for block in blocks:
        block = block.strip()
        if not block or len(block) < 15:
            continue

        line_match = _LINE_REF_PATTERN.search(block)
        if not line_match:
            continue

        line_start = int(line_match.group(1))
        line_end = int(line_match.group(2)) if line_match.group(2) else line_start

        block_lower = block.lower()
        severity = "medium"
        for kw, sev in _SEVERITY_KEYWORDS.items():
            if kw in block_lower:
                severity = sev
                break

        category = "style"
        for kw, cat in _CATEGORY_KEYWORDS.items():
            if kw in block_lower:
                category = cat
                break

        findings.append(Finding(
            file_path=file_path,
            line_start=line_start,
            line_end=line_end,
            category=category,
            severity=severity,
            description=_block_description(block),
        ))

    return findings


def _block_description(block: str) -> str:
    """Derive a clean Finding description from one issue block.

    Prefers the ``**Claim:**`` text when present (the grounded-prose format),
    stripping the markdown label. Otherwise falls back to the whole block.
    Markdown emphasis/heading markers are trimmed and whitespace collapsed,
    then truncated to 200 chars.
    """
    claim = _CLAIM_PATTERN.search(block)
    text = claim.group(1) if claim else block
    text = re.sub(r"\s+", " ", text).strip()
    text = text.lstrip("#*-: `").strip()
    return text[:200]


async def validate_findings(
    findings: list[dict],
    file_path: str,
    file_content: str,
) -> List[Finding]:
    """Validate pre-structured findings from the schema-constrained review.

    Each finding is checked against the file content via ``_validate_verbatim``.
    Findings whose ``verbatim_excerpt`` doesn't appear in the cited line range
    are logged as WARNING and dropped. Other findings in the same batch still
    persist.

    Args:
        findings: A list of dicts with keys matching ``_REQUIRED_KEYS``.
        file_path: Relative path of the reviewed file (for Finding records).
        file_content: Full file text for verbatim-excerpt checks.

    Returns a list of valid ``Finding`` dataclass instances (never raises).
    """
    valid: List[Finding] = []
    for entry in findings:
        if not _validate_verbatim(entry, file_content):
            log.warning(
                "verbatim_excerpt %r not found in cited range %s:%s-%s; dropping finding",
                entry.get("verbatim_excerpt"),
                file_path,
                entry.get("line_start"),
                entry.get("line_end"),
            )
            continue
        finding = _parse_finding(entry, file_path)
        if finding is not None:
            valid.append(finding)
        else:
            log.warning("Skipping malformed finding entry: %s", entry)
    return valid
