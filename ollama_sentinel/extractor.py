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


# Matches a line reference in the pre-grounding free-form style ("line 5",
# "lines 5-10"), the grounded-prose degrade style the prompt asks for
# ("Line Range: `line_start..line_end`", e.g. "Line Range: `1..2`"), or the
# terse style some models emit ("L31", "L36–38" — seen from kimi-k3). After a
# spelled-out label, markdown punctuation (** ` : =) may sit between the label
# and the first number; the terse `L` must abut its number and is
# case-sensitive (a lowercase `l33t` or embedded `URL31` is not a line ref);
# the range separator may be ``..``, a hyphen/dash, or the word "to".
_LINE_REF_PATTERN = re.compile(
    r"(?:(?:line[\s_]*range|line[\s_]*start|lines?)[\s:=*`'\"]*|(?-i:\bL))(\d+)"
    r"(?:\s*(?:\.\.|[-–—]|to)\s*[`'\"]*(?-i:L?)(\d+))?",
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

# Matches fenced code blocks (including unclosed trailing fences) and inline
# backtick spans so quoted code identifiers are excluded from keyword scans.
_FENCED_CODE_PATTERN = re.compile(r"```[\s\S]*?(?:```|$)")
_INLINE_CODE_PATTERN = re.compile(r"`[^`\n]+`")

# Explicit labels ("**Severity:** High", "Category: security") take precedence
# over fallback keyword scanning.
_EXPLICIT_SEVERITY_PATTERN = re.compile(
    r"\bseverity\b\s*[:=\-]\s*[*`'\"]*\s*"
    r"(critical|severe|high|important|medium|moderate|low|minor|style|nit)\b",
    re.IGNORECASE,
)
_EXPLICIT_CATEGORY_PATTERN = re.compile(
    r"\bcategory\b\s*[:=\-]\s*[*`'\"]*\s*"
    r"(bug|error|security|performance|style|design)\b",
    re.IGNORECASE,
)

# Negated severity phrases ("not critical", "non-critical") stripped before
# fallback keyword matching so they don't promote a minor issue to critical.
_NEGATED_SEVERITY_PATTERN = re.compile(
    r"\b(?:not|non)[\s\-]+(?:critical|severe|high|important)\b",
    re.IGNORECASE,
)

# Blocks where the model explicitly clears a construct ("non-issue",
# "no SQL injection risk", "not a bug") should not be persisted as findings.
_NON_ISSUE_PATTERN = re.compile(
    r"\b(?:non-issue|no\s+issues?\b|not\s+a\s+(?:bug|problem|vulnerability|issue)|"
    r"no\s+(?:sql\s+|xss\s+|command\s+)?injection\s+risk)\b",
    re.IGNORECASE,
)

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


def _strip_code_spans(text: str) -> str:
    """Remove fenced code blocks and inline backtick spans from *text*.

    Used before keyword-scanning a review block for severity and category so
    identifiers inside quoted source (e.g. a CSS ``.critical`` class, a
    ``{ critical_high: 'HH' }`` map, or ``catch (error)``) do not leak into
    the finding's classification.
    """
    without_fences = _FENCED_CODE_PATTERN.sub(" ", text)
    return _INLINE_CODE_PATTERN.sub(" ", without_fences)


def synthesize_excerpt(file_content: str, line_start: int, line_end: int) -> str:
    """Slice 1-based inclusive lines ``[line_start..line_end]`` from *file_content*.

    Returns ``""`` when *file_content* is empty or the cited range does not
    fall within the file (``line_start < 1``, ``line_end < line_start``, or
    ``line_start > len(lines)``). When ``line_end`` overshoots the end of
    the file, clamps to ``len(lines)``.
    """
    if not file_content:
        return ""
    lines = file_content.splitlines()
    if line_start < 1 or line_end < line_start or line_start > len(lines):
        return ""
    end = min(line_end, len(lines))
    return "\n".join(lines[line_start - 1 : end])


_PROMPT_LINE_PREFIX_RE = re.compile(r"^\s*(?:\d+:\s?(?:\+(?![+=]))?|\+(?![+=]))")


def _strip_prompt_prefixes(excerpt: str) -> str:
    """Strip accidental ``N: `` or unified-diff ``+`` prefixes per line."""
    if not excerpt:
        return excerpt
    return "\n".join(_PROMPT_LINE_PREFIX_RE.sub("", ln) for ln in excerpt.splitlines())


def _validate_verbatim(finding: dict, file_content: str) -> bool:
    """Return True if the finding's verbatim_excerpt is found in the cited lines.

    Slices ``file_content`` at the finding's ``line_start``..``line_end``,
    normalises whitespace (collapse runs to single spaces, strip), and checks
    that the finding's ``verbatim_excerpt`` (also whitespace-normalised) is
    contained in that slice. Falls back to stripping accidental ``N: `` or diff
    ``+`` line prefixes when the model copied them from the prompt.
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

    raw_excerpt = str(finding.get("verbatim_excerpt", ""))
    excerpt = _normalise(raw_excerpt)
    if not excerpt:
        return False
    norm_slice = _normalise(slice_text)
    if excerpt in norm_slice:
        return True
    stripped_excerpt = _normalise(_strip_prompt_prefixes(raw_excerpt))
    if stripped_excerpt and stripped_excerpt in norm_slice:
        return True
    return False


def extract_findings_legacy(
    review_text: str,
    file_path: str,
    file_content: str = "",
) -> List[Finding]:
    """Regex-based extraction from free-form review markdown.

    The prose extraction path: parses issue blocks (headings, bullets, or
    numbered items) that reference line numbers, classifies severity and
    category from the prose (excluding fenced and inline code spans so quoted
    identifiers like ``.critical`` or ``error`` don't skew ratings), and — when
    *file_content* is provided — synthesizes ``verbatim_excerpt`` by slicing the
    cited line range from the file so downstream relocation, ``fix``, and
    ``prune`` remain operable.
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

        prose_lower = _strip_code_spans(block).lower()
        if _NON_ISSUE_PATTERN.search(prose_lower):
            continue

        explicit_sev = _EXPLICIT_SEVERITY_PATTERN.search(block)
        if explicit_sev:
            severity = _SEVERITY_KEYWORDS[explicit_sev.group(1).lower()]
        else:
            cleaned_sev_prose = _NEGATED_SEVERITY_PATTERN.sub(" ", prose_lower)
            severity = "medium"
            for kw, sev in _SEVERITY_KEYWORDS.items():
                if kw in cleaned_sev_prose:
                    severity = sev
                    break

        explicit_cat = _EXPLICIT_CATEGORY_PATTERN.search(block)
        if explicit_cat:
            category = _CATEGORY_KEYWORDS[explicit_cat.group(1).lower()]
        else:
            category = "style"
            for kw, cat in _CATEGORY_KEYWORDS.items():
                if kw in prose_lower:
                    category = cat
                    break

        excerpt = synthesize_excerpt(file_content, line_start, line_end)

        findings.append(Finding(
            file_path=file_path,
            line_start=line_start,
            line_end=line_end,
            category=category,
            severity=severity,
            description=_block_description(block),
            verbatim_excerpt=excerpt,
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
            if finding.verbatim_excerpt and finding.verbatim_excerpt not in file_content:
                cleaned = _strip_prompt_prefixes(finding.verbatim_excerpt)
                if cleaned:
                    finding.verbatim_excerpt = cleaned
            valid.append(finding)
        else:
            log.warning("Skipping malformed finding entry: %s", entry)
    return valid
