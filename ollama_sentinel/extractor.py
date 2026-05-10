"""
Finding extractor — parse review markdown into structured Finding records.

Sends a focused extraction prompt to the Ollama model and parses the
JSON response into a list of Finding dataclass instances.
"""
import json
import logging
import re
from typing import List

from .violation_db import Finding

log = logging.getLogger("ollama-sentinel")

_REQUIRED_KEYS = {"line_start", "line_end", "category", "severity", "verbatim_excerpt", "description"}

# Feature flag: set True to re-enable the regex fallback path
# (_extract_from_markdown) which was removed in the reviewer-grounding
# refactor. Currently False — the schema-constrained model output
# replaces the old two-stage extraction pipeline.
_USE_REGEX_FALLBACK = False


def _strip_code_fences(text: str) -> str:
    """Remove markdown code fences that LLMs often wrap around JSON."""
    text = text.strip()
    # Remove ```json ... ``` or ``` ... ```
    m = re.match(r"^```(?:json)?\s*\n?(.*?)```\s*$", text, re.DOTALL)
    if m:
        return m.group(1).strip()
    return text


def _loads_findings_json(text: str):
    """Parse finding JSON from model output.

    Accepts either a raw array or an object containing a ``findings`` array.
    If the model emits prose around JSON, tries to recover the first JSON-ish
    array/object before falling back.
    """
    cleaned = _strip_code_fences(text)
    candidates = [cleaned]

    array_start = cleaned.find("[")
    array_end = cleaned.rfind("]")
    if array_start != -1 and array_end > array_start:
        candidates.append(cleaned[array_start:array_end + 1])

    object_start = cleaned.find("{")
    object_end = cleaned.rfind("}")
    if object_start != -1 and object_end > object_start:
        candidates.append(cleaned[object_start:object_end + 1])

    last_error = None
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except (json.JSONDecodeError, TypeError) as e:
            last_error = e
            continue
        if isinstance(parsed, dict) and isinstance(parsed.get("findings"), list):
            return parsed["findings"]
        return parsed
    if last_error:
        raise last_error
    raise json.JSONDecodeError("No JSON object or array found", cleaned, 0)


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


_LINE_REF_PATTERN = re.compile(
    r"(?:line|lines?)\s*(\d+)(?:\s*[-–]\s*(\d+))?",
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


def _extract_from_markdown(review_text: str, file_path: str) -> List[Finding]:
    """Regex fallback: extract findings from review markdown when LLM JSON fails.

    Looks for bullet points or numbered items that reference line numbers.
    """
    findings: List[Finding] = []
    # Split into bullet/numbered items
    items = re.split(r"\n\s*(?:[-*]|\d+\.)\s+", review_text)

    for item in items:
        item = item.strip()
        if not item or len(item) < 15:
            continue

        line_match = _LINE_REF_PATTERN.search(item)
        if not line_match:
            continue

        line_start = int(line_match.group(1))
        line_end = int(line_match.group(2)) if line_match.group(2) else line_start

        item_lower = item.lower()
        severity = "medium"
        for kw, sev in _SEVERITY_KEYWORDS.items():
            if kw in item_lower:
                severity = sev
                break

        category = "style"
        for kw, cat in _CATEGORY_KEYWORDS.items():
            if kw in item_lower:
                category = cat
                break

        description = item[:200].replace("\n", " ").strip()
        findings.append(Finding(
            file_path=file_path,
            line_start=line_start,
            line_end=line_end,
            category=category,
            severity=severity,
            description=description,
        ))

    return findings


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
