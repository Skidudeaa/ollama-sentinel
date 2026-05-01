"""
Finding extractor — parse review markdown into structured Finding records.

Sends a focused extraction prompt to the Ollama model and parses the
JSON response into a list of Finding dataclass instances.
"""
import json
import logging
import re
from typing import List

from .processor import OllamaClient
from .violation_db import Finding

log = logging.getLogger("ollama-sentinel")

_REQUIRED_KEYS = {"line_start", "line_end", "category", "severity", "description"}

_EXTRACTION_PROMPT = (
    "Extract code review findings from the text below as a JSON array. "
    'Each finding: {{"line_start": int, "line_end": int, "category": str, '
    '"severity": str, "description": str}}. '
    "Categories: bug, security, performance, style, design. "
    "Severities: critical, high, medium, low. "
    "If no findings, return []. Return ONLY the JSON array.\n\n"
    "REVIEW:\n{review_text}"
)


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


async def extract_findings(
    review_text: str,
    file_path: str,
    ollama_client: OllamaClient,
    model_role: str = "default",
) -> List[Finding]:
    """Extract structured findings from free-form review text.

    Primary: sends a focused extraction prompt to the Ollama model and parses
    the JSON response. Fallback: regex-based extraction from the original
    review markdown when the LLM doesn't produce valid JSON.

    Returns an empty list on complete failure (never raises).
    """
    prompt = _EXTRACTION_PROMPT.format(review_text=review_text)

    try:
        response = await ollama_client.generate_review(
            model_role,
            prompt,
            response_format="json",
        )
    except Exception:
        log.warning("Ollama API error during finding extraction; trying regex fallback")
        return _extract_from_markdown(review_text, file_path)


    try:
        parsed = _loads_findings_json(response)
    except (json.JSONDecodeError, TypeError):
        log.warning("Malformed JSON from extraction model; trying regex fallback")
        return _extract_from_markdown(review_text, file_path)

    if not isinstance(parsed, list):
        log.warning("Extraction response is not a JSON array; trying regex fallback")
        return _extract_from_markdown(review_text, file_path)

    findings: List[Finding] = []
    for entry in parsed:
        finding = _parse_finding(entry, file_path)
        if finding is not None:
            findings.append(finding)
        else:
            log.warning("Skipping malformed finding entry: %s", entry)

    # If LLM produced empty results but review has content, try regex too
    if not findings and len(review_text) > 100:
        regex_findings = _extract_from_markdown(review_text, file_path)
        if regex_findings:
            log.info("LLM extraction empty; regex fallback found %d findings", len(regex_findings))
            return regex_findings

    return findings
