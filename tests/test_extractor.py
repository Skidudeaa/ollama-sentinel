"""Tests for ollama_sentinel.extractor — finding extraction from review text."""
import json

import pytest
from pytest_httpx import HTTPXMock
from tenacity import wait_none

from ollama_sentinel.extractor import extract_findings
from ollama_sentinel.models import OllamaConfig, OllamaModelConfig
from ollama_sentinel.processor import OllamaClient
from ollama_sentinel.violation_db import Finding


OLLAMA_CHAT_URL = "http://localhost:11434/api/chat"


@pytest.fixture
def ollama_config():
    """Minimal Ollama config dict matching what OllamaClient expects."""
    return OllamaConfig(
        host="http://localhost:11434",
        models={
            "default": OllamaModelConfig(
                name="test-model", system_prompt="Review code."
            ),
        },
    ).model_dump()


# ---------------------------------------------------------------------------
# Happy-path tests
# ---------------------------------------------------------------------------


class TestExtractFindingsHappyPath:
    """Tests for well-formed extraction results."""

    async def test_valid_json_array_with_three_findings(
        self, ollama_config, httpx_mock: HTTPXMock
    ):
        """A valid JSON array with 3 findings produces 3 Finding objects."""
        findings_json = json.dumps([
            {
                "line_start": 10,
                "line_end": 12,
                "category": "bug",
                "severity": "high",
                "description": "Null pointer dereference",
            },
            {
                "line_start": 25,
                "line_end": 25,
                "category": "style",
                "severity": "low",
                "description": "Missing docstring",
            },
            {
                "line_start": 40,
                "line_end": 45,
                "category": "security",
                "severity": "critical",
                "description": "SQL injection via string format",
            },
        ])

        httpx_mock.add_response(
            url=OLLAMA_CHAT_URL,
            json={"message": {"content": findings_json}},
        )

        client = OllamaClient(ollama_config)
        try:
            results = await extract_findings(
                review_text="some review text",
                file_path="src/app.py",
                ollama_client=client,
            )
        finally:
            await client.close()

        assert len(results) == 3
        assert all(isinstance(f, Finding) for f in results)

        # Verify file_path is filled in from the parameter, not the JSON
        assert all(f.file_path == "src/app.py" for f in results)

        # Spot-check individual findings
        assert results[0].category == "bug"
        assert results[0].severity == "high"
        assert results[0].line_start == 10
        assert results[0].line_end == 12
        assert results[0].description == "Null pointer dereference"

        assert results[2].category == "security"
        assert results[2].severity == "critical"

    async def test_empty_json_array_returns_empty_list(
        self, ollama_config, httpx_mock: HTTPXMock
    ):
        """Model returning '[]' produces an empty list."""
        httpx_mock.add_response(
            url=OLLAMA_CHAT_URL,
            json={"message": {"content": "[]"}},
        )

        client = OllamaClient(ollama_config)
        try:
            results = await extract_findings(
                review_text="clean code, no issues",
                file_path="src/clean.py",
                ollama_client=client,
            )
        finally:
            await client.close()

        assert results == []

    async def test_json_wrapped_in_code_fences(
        self, ollama_config, httpx_mock: HTTPXMock
    ):
        """Model returning JSON inside ```json ... ``` fences still parses."""
        inner = json.dumps([
            {
                "line_start": 1,
                "line_end": 1,
                "category": "style",
                "severity": "low",
                "description": "Trailing whitespace",
            },
        ])
        wrapped = f"```json\n{inner}\n```"

        httpx_mock.add_response(
            url=OLLAMA_CHAT_URL,
            json={"message": {"content": wrapped}},
        )

        client = OllamaClient(ollama_config)
        try:
            results = await extract_findings(
                review_text="review text",
                file_path="src/fmt.py",
                ollama_client=client,
            )
        finally:
            await client.close()

        assert len(results) == 1
        assert results[0].description == "Trailing whitespace"

    async def test_object_wrapped_findings_array(
        self, ollama_config, httpx_mock: HTTPXMock
    ):
        """Model returning {"findings": [...]} is accepted."""
        response_json = json.dumps({
            "findings": [
                {
                    "line_start": 7,
                    "line_end": 9,
                    "category": "bug",
                    "severity": "medium",
                    "description": "Bounds check is missing",
                },
            ],
        })

        httpx_mock.add_response(
            url=OLLAMA_CHAT_URL,
            json={"message": {"content": response_json}},
        )

        client = OllamaClient(ollama_config)
        try:
            results = await extract_findings(
                review_text="review text",
                file_path="src/bounds.py",
                ollama_client=client,
            )
        finally:
            await client.close()

        assert len(results) == 1
        assert results[0].line_start == 7
        request_body = json.loads(httpx_mock.get_requests()[0].content)
        assert request_body["format"] == "json"


# ---------------------------------------------------------------------------
# Edge-case tests
# ---------------------------------------------------------------------------


class TestExtractFindingsEdgeCases:
    """Tests for malformed or partial responses."""

    async def test_malformed_json_returns_empty_list(
        self, ollama_config, httpx_mock: HTTPXMock
    ):
        """Completely invalid JSON returns an empty list, no crash."""
        httpx_mock.add_response(
            url=OLLAMA_CHAT_URL,
            json={"message": {"content": "This is not JSON at all {{{"}},
        )

        client = OllamaClient(ollama_config)
        try:
            results = await extract_findings(
                review_text="review text",
                file_path="src/bad.py",
                ollama_client=client,
            )
        finally:
            await client.close()

        assert results == []

    async def test_missing_fields_skips_bad_entries(
        self, ollama_config, httpx_mock: HTTPXMock
    ):
        """Entries missing required fields are skipped; valid ones are kept."""
        findings_json = json.dumps([
            {
                # Valid entry
                "line_start": 5,
                "line_end": 5,
                "category": "bug",
                "severity": "medium",
                "description": "Off-by-one error",
            },
            {
                # Missing 'severity' and 'description'
                "line_start": 20,
                "line_end": 22,
                "category": "performance",
            },
            {
                # Missing everything except description
                "description": "Orphan finding",
            },
            {
                # Valid entry
                "line_start": 30,
                "line_end": 35,
                "category": "design",
                "severity": "low",
                "description": "Consider extracting a method",
            },
        ])

        httpx_mock.add_response(
            url=OLLAMA_CHAT_URL,
            json={"message": {"content": findings_json}},
        )

        client = OllamaClient(ollama_config)
        try:
            results = await extract_findings(
                review_text="review text",
                file_path="src/mixed.py",
                ollama_client=client,
            )
        finally:
            await client.close()

        # Only 2 valid entries should survive
        assert len(results) == 2
        assert results[0].description == "Off-by-one error"
        assert results[1].description == "Consider extracting a method"
        assert all(f.file_path == "src/mixed.py" for f in results)


# ---------------------------------------------------------------------------
# Error-path tests
# ---------------------------------------------------------------------------


class TestExtractFindingsErrors:
    """Tests for API and network errors."""

    async def test_http_500_exhausts_retries_returns_empty(
        self, ollama_config, httpx_mock: HTTPXMock
    ):
        """HTTP 500 on all 5 retry attempts results in an empty list."""
        # The retry decorator does stop_after_attempt(5), so queue 5 failures.
        for _ in range(5):
            httpx_mock.add_response(url=OLLAMA_CHAT_URL, status_code=500)

        client = OllamaClient(ollama_config)
        # Disable wait between retries for fast test execution.
        # The @retry decorator is on generate_with_model (the HTTP layer).
        client.generate_with_model.retry.wait = wait_none()
        try:
            results = await extract_findings(
                review_text="review text",
                file_path="src/fail.py",
                ollama_client=client,
            )
        finally:
            await client.close()

        assert results == []
        assert len(httpx_mock.get_requests()) == 5


# ---------------------------------------------------------------------------
# Regex fallback tests
# ---------------------------------------------------------------------------

from ollama_sentinel.extractor import _extract_from_markdown


class TestRegexFallback:
    """Tests for the regex-based markdown fallback extractor."""

    def test_extracts_from_bullet_points_with_line_refs(self):
        review = """## Code Review

- **Line 42**: Null pointer dereference — `user` may be None here
- **Line 15-20**: SQL injection vulnerability via string formatting
- **Line 88**: Minor style issue — consider renaming variable
"""
        findings = _extract_from_markdown(review, "app.py")
        assert len(findings) == 3
        assert findings[0].line_start == 42
        assert findings[0].file_path == "app.py"
        assert findings[1].line_start == 15
        assert findings[1].line_end == 20
        assert findings[1].category == "security"

    def test_extracts_from_numbered_list(self):
        review = """1. Line 10: Bug — off-by-one error in loop condition
2. Line 30: Performance issue — this runs in O(n^2)
3. No line reference here, should be skipped
"""
        findings = _extract_from_markdown(review, "x.py")
        assert len(findings) == 2
        assert findings[0].line_start == 10
        assert findings[0].category == "bug"
        assert findings[1].category == "performance"

    def test_no_line_references_returns_empty(self):
        review = "This code looks great! No issues found."
        findings = _extract_from_markdown(review, "clean.py")
        assert findings == []

    def test_severity_detection(self):
        review = """- Line 5: Critical security vulnerability — SQL injection
- Line 10: Minor nit — trailing whitespace
"""
        findings = _extract_from_markdown(review, "a.py")
        assert findings[0].severity == "critical"
        assert findings[1].severity == "low"

    async def test_fallback_used_on_malformed_json(self, ollama_config, httpx_mock: HTTPXMock):
        """When LLM returns garbage, regex fallback extracts from original review."""
        review_text = """## Review
- Line 42: Bug — possible null pointer
- Line 88: Style — rename this variable
"""
        httpx_mock.add_response(
            url=OLLAMA_CHAT_URL,
            json={"message": {"content": "Sorry, I can't produce JSON"}},
        )
        client = OllamaClient(ollama_config)
        try:
            results = await extract_findings(review_text, "app.py", client)
        finally:
            await client.close()
        # Regex fallback should find the 2 items from the original review
        assert len(results) == 2
        assert results[0].line_start == 42
