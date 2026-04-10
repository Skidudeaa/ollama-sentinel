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


async def extract_findings(
    review_text: str,
    file_path: str,
    ollama_client: OllamaClient,
    model_role: str = "default",
) -> List[Finding]:
    """Extract structured findings from free-form review text.

    Sends a focused extraction prompt to the Ollama model and parses the
    JSON response into Finding objects.

    Args:
        review_text: The raw review markdown/text to extract from.
        file_path: Source file path to attach to each Finding.
        ollama_client: An initialised OllamaClient instance.
        model_role: Ollama model role to use for extraction.

    Returns:
        A list of Finding objects.  Returns an empty list on any error
        (malformed JSON, network failure, etc.).
    """
    prompt = _EXTRACTION_PROMPT.format(review_text=review_text)

    try:
        response = await ollama_client.generate_review(model_role, prompt)
    except Exception:
        log.warning("Ollama API error during finding extraction; returning empty list")
        return []

    cleaned = _strip_code_fences(response)

    try:
        parsed = json.loads(cleaned)
    except (json.JSONDecodeError, TypeError):
        log.warning("Malformed JSON from extraction model; returning empty list")
        return []

    if not isinstance(parsed, list):
        log.warning("Extraction response is not a JSON array; returning empty list")
        return []

    findings: List[Finding] = []
    for entry in parsed:
        finding = _parse_finding(entry, file_path)
        if finding is not None:
            findings.append(finding)
        else:
            log.warning("Skipping malformed finding entry: %s", entry)

    return findings
