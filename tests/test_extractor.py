"""Tests for ollama_sentinel.extractor — finding validation from schema output."""
import pytest

from ollama_sentinel.extractor import (
    _validate_verbatim,
    extract_findings_legacy,
    validate_findings,
)
from ollama_sentinel.violation_db import Finding


# ---------------------------------------------------------------------------
# _validate_verbatim tests
# ---------------------------------------------------------------------------


class TestValidateVerbatim:
    """Tests for the _validate_verbatim helper."""

    def test_exact_match(self):
        """A verbatim excerpt that matches the cited lines passes."""
        content = "line one\nline two\nline three\n"
        finding = {
            "line_start": 2,
            "line_end": 3,
            "verbatim_excerpt": "line two\nline three",
        }
        assert _validate_verbatim(finding, content) is True

    def test_excerpt_within_normalised_whitespace(self):
        """Whitespace-normalised excerpt within whitespace-normalised slice passes."""
        content = "def  foo():\n    return  42\n"
        finding = {
            "line_start": 1,
            "line_end": 2,
            "verbatim_excerpt": "def foo():   return 42",
        }
        assert _validate_verbatim(finding, content) is True

    def test_excerpt_not_found(self):
        """An excerpt that does not appear in the cited lines fails."""
        content = "line one\nline two\nline three\n"
        finding = {
            "line_start": 2,
            "line_end": 2,
            "verbatim_excerpt": "line three",
        }
        assert _validate_verbatim(finding, content) is False

    def test_empty_excerpt_fails(self):
        """An empty verbatim_excerpt always fails."""
        content = "line one\nline two\n"
        finding = {
            "line_start": 1,
            "line_end": 2,
            "verbatim_excerpt": "",
        }
        assert _validate_verbatim(finding, content) is False

    def test_line_start_out_of_range(self):
        """line_start beyond the file length returns False."""
        content = "only one line\n"
        finding = {
            "line_start": 10,
            "line_end": 10,
            "verbatim_excerpt": "anything",
        }
        assert _validate_verbatim(finding, content) is False

    def test_non_integer_lines(self):
        """Non-integer line values return False without crashing."""
        content = "some\nlines\n"
        finding = {
            "line_start": "abc",
            "line_end": "def",
            "verbatim_excerpt": "some",
        }
        assert _validate_verbatim(finding, content) is False


# ---------------------------------------------------------------------------
# validate_findings — happy path
# ---------------------------------------------------------------------------


class TestValidateFindingsHappyPath:
    """Tests for well-formed findings that pass verbatim validation."""

    def test_single_valid_finding(self):
        """A single valid finding with matching verbatim_excerpt returns one Finding."""
        findings = [
            {
                "line_start": 1,
                "line_end": 1,
                "category": "bug",
                "severity": "high",
                "verbatim_excerpt": "x = None",
                "description": "Null pointer risk",
            },
        ]
        content = "x = None\n"
        import asyncio
        result = asyncio.run(validate_findings(findings, "app.py", content))

        assert len(result) == 1
        assert isinstance(result[0], Finding)
        assert result[0].file_path == "app.py"
        assert result[0].category == "bug"
        assert result[0].severity == "high"
        assert result[0].verbatim_excerpt == "x = None"
        assert result[0].description == "Null pointer risk"

    def test_multiple_valid_findings_all_pass(self):
        """Multiple findings whose excerpts match all return as Finding objects."""
        findings = [
            {
                "line_start": 1,
                "line_end": 1,
                "category": "style",
                "severity": "low",
                "verbatim_excerpt": "x = 1",
                "description": "Trailing whitespace",
            },
            {
                "line_start": 3,
                "line_end": 5,
                "category": "performance",
                "severity": "medium",
                "verbatim_excerpt": "for i in range",
                "description": "Use comprehension",
            },
        ]
        content = "x = 1\ny = 2\nfor i in range(10):\n    pass\nz = 3\n"
        import asyncio
        result = asyncio.run(validate_findings(findings, "mod.py", content))

        assert len(result) == 2
        assert result[0].category == "style"
        assert result[1].category == "performance"

    def test_empty_findings_list(self):
        """An empty findings list produces an empty result list."""
        import asyncio
        result = asyncio.run(validate_findings([], "clean.py", "any content"))
        assert result == []

    def test_finding_with_no_findings_but_prose_persisted(self):
        """findings: [] persists no findings (tested via validate_findings returning [])."""
        import asyncio
        result = asyncio.run(validate_findings([], "some.py", "content"))
        assert result == []


# ---------------------------------------------------------------------------
# validate_findings — verbatim failure path
# ---------------------------------------------------------------------------


class TestValidateFindingsVerbatimFailure:
    """Tests where findings fail the verbatim check."""

    def test_mismatched_verbatim_is_dropped_with_warning(self, caplog):
        """A finding whose verbatim_excerpt doesn't match is dropped with WARNING;
        other valid findings in the same batch still pass."""
        import asyncio
        findings = [
            {
                "line_start": 1,
                "line_end": 1,
                "category": "bug",
                "severity": "high",
                "verbatim_excerpt": "x = None",
                "description": "Null pointer risk",
            },
            {
                "line_start": 3,
                "line_end": 3,
                "category": "style",
                "severity": "low",
                "verbatim_excerpt": "bad line",
                "description": "Does not match",
            },
            {
                "line_start": 5,
                "line_end": 5,
                "category": "performance",
                "severity": "medium",
                "verbatim_excerpt": "quick_sort(data)",
                "description": "Use built-in sorted",
            },
        ]
        content = "x = None\ny = 2\nz = 3\nw = 4\nquick_sort(data)\n"

        with caplog.at_level("WARNING"):
            result = asyncio.run(validate_findings(findings, "test.py", content))

        # Only the valid findings survive
        assert len(result) == 2
        assert result[0].category == "bug"
        assert result[1].category == "performance"

        # A WARNING was logged for the dropped finding
        warning_messages = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
        assert any("not found in cited range" in m for m in warning_messages)

    def test_all_mismatched_produces_empty(self, caplog):
        """When every finding fails verbatim, the result is an empty list."""
        import asyncio
        findings = [
            {
                "line_start": 1,
                "line_end": 1,
                "category": "bug",
                "severity": "high",
                "verbatim_excerpt": "nonexistent",
                "description": "Never in file",
            },
        ]
        content = "actual content\n"

        with caplog.at_level("WARNING"):
            result = asyncio.run(validate_findings(findings, "fake.py", content))

        assert result == []


# ---------------------------------------------------------------------------
# validate_findings — malformed entries
# ---------------------------------------------------------------------------


class TestValidateFindingsMalformed:
    """Tests for entries with missing or invalid fields."""

    def test_missing_required_keys_skipped(self):
        """Entries missing required keys like severity are skipped; valid ones pass."""
        import asyncio
        findings = [
            {
                "line_start": 1,
                "line_end": 1,
                "category": "bug",
                "severity": "medium",
                "verbatim_excerpt": "off_by_one(items)",
                "description": "Off-by-one error",
            },
            {
                # Missing severity and verbatim_excerpt
                "line_start": 2,
                "line_end": 2,
                "category": "performance",
                "description": "Orphan finding",
            },
            {
                "line_start": 3,
                "line_end": 8,
                "category": "design",
                "severity": "low",
                "verbatim_excerpt": "def process(self",
                "description": "Consider extracting a method",
            },
        ]
        content = (
            "off_by_one(items)\n"
            "extra line\n"
            "def process(self):\n"
            "    pass\n"
            "    pass\n"
            "    pass\n"
            "    pass\n"
            "    pass\n"
            "    pass\n"
        )

        result = asyncio.run(validate_findings(findings, "mixed.py", content))

        # The middle entry (finding 2) lacks severity and verbatim_excerpt,
        # so _parse_finding rejects it because _REQUIRED_KEYS is not satisfied.
        assert len(result) == 2
        assert result[0].description == "Off-by-one error"
        assert result[1].description == "Consider extracting a method"


# ---------------------------------------------------------------------------
# extract_findings_legacy — the prose-parsing degrade path
#
# This is the path that runs when a model ignores Ollama's `format` schema and
# returns markdown prose instead of JSON (every `:cloud` model, and reasoning
# models like qwen3.6 that emit a thinking preamble). The prompt asks for a
# per-issue block carrying `Line Range: line_start..line_end`, a verbatim
# excerpt, and a claim — so the extractor must parse THAT canonical format, not
# only the pre-grounding `line 5` / `lines 5-10` style.
# ---------------------------------------------------------------------------


# Captured verbatim from a real `ollama-sentinel review` run whose grounded
# review degraded to prose (qwen3.6:35b). Do not "clean up" — the whole point
# is that the extractor handles the format models actually emit.
_GROUNDED_PROSE = """Here is the code review for `_smoke/buggy.py`:

### 1. Missing Input Validation in `divide`
**Line Range:** `1..2`
**Excerpt:**
```python
def divide(a, b):
    return a / b
```
**Claim:** The function lacks validation for division by zero. If `b` is `0`, \
this will raise an unhandled `ZeroDivisionError`, a common runtime bug.

### 2. Off-by-One Error in `get_user`
**Line Range:** `5..5`
**Excerpt:**
```python
    return users[idx + 1]
```
**Claim:** The index calculation `idx + 1` is likely an off-by-one error and \
risks an `IndexError`. It should likely be `users[idx]`.

### 3. Missing Type Hints and Docstrings
**Line Range:** `1..6`
**Excerpt:**
```python
def divide(a, b):
    return a / b
```
**Claim:** The functions lack type hints and docstrings, reducing readability.
"""


class TestExtractFindingsLegacyGroundedProse:
    """The degrade path must parse the grounded per-issue markdown format."""

    def test_multi_issue_grounded_prose(self):
        """Three `### N.` issue blocks → three findings with correct line ranges."""
        findings = extract_findings_legacy(_GROUNDED_PROSE, "_smoke/buggy.py")
        ranges = [(f.line_start, f.line_end) for f in findings]
        assert ranges == [(1, 2), (5, 5), (1, 6)]
        assert all(f.file_path == "_smoke/buggy.py" for f in findings)

    def test_description_comes_from_claim(self):
        """The Finding description carries the claim text, not the scaffolding."""
        findings = extract_findings_legacy(_GROUNDED_PROSE, "_smoke/buggy.py")
        assert findings[0].description.startswith("The function lacks validation")
        # No leftover markdown label/scaffolding leaks into the description.
        assert "Line Range" not in findings[0].description
        assert "**" not in findings[0].description

    def test_line_range_double_dot_and_backticks(self):
        """`Line Range: \\`A..B\\`` (double-dot, backtick-wrapped) parses."""
        block = "### 1. Thing\n**Line Range:** `12..20`\n**Claim:** A problem."
        findings = extract_findings_legacy(block, "x.py")
        assert len(findings) == 1
        assert (findings[0].line_start, findings[0].line_end) == (12, 20)

    def test_preamble_without_line_refs_yields_nothing(self):
        """Prose with no line references produces no findings (no false positives)."""
        text = "Here is the code review for `x.py`:\n\nLooks clean overall."
        assert extract_findings_legacy(text, "x.py") == []


class TestExtractFindingsLegacyClassicFormat:
    """Regression guard: the pre-grounding `line N` / bullet format still parses."""

    def test_bullet_line_single(self):
        text = "- Line 5: missing null check before dereference (bug)."
        findings = extract_findings_legacy(text, "a.py")
        assert len(findings) == 1
        assert (findings[0].line_start, findings[0].line_end) == (5, 5)
        assert findings[0].category == "bug"

    def test_bullet_lines_hyphen_range(self):
        text = "- Lines 12-15: slow nested loop, a performance concern."
        findings = extract_findings_legacy(text, "a.py")
        assert len(findings) == 1
        assert (findings[0].line_start, findings[0].line_end) == (12, 15)
        assert findings[0].category == "performance"

    def test_numbered_items_multiple(self):
        text = (
            "1. Line 3: unchecked index is a bug.\n"
            "2. Line 8: naming is unclear, a style nit.\n"
        )
        findings = extract_findings_legacy(text, "a.py")
        assert [(f.line_start, f.line_end) for f in findings] == [(3, 3), (8, 8)]


# ---------------------------------------------------------------------------
# Schema failure fallback tests (processor-level)
# ---------------------------------------------------------------------------


class TestParseReviewResponse:
    """Tests for FileProcessor._parse_review_response via generate_review."""

    async def test_schema_conformant_response(self, sentinel_config, tmp_path, httpx_mock):
        """A schema-conformant model response with one valid finding round-trips."""
        from ollama_sentinel.processor import FileProcessor, FileChange
        from watchfiles import Change
        import json

        source = tmp_path / "app.py"
        source.write_text("x = None\n")

        response_data = {
            "summary": "Found one issue.",
            "findings": [
                {
                    "line_start": 1,
                    "line_end": 1,
                    "category": "bug",
                    "severity": "high",
                    "verbatim_excerpt": "x = None",
                    "description": "Null pointer risk",
                },
            ],
        }
        httpx_mock.add_response(
            url="http://localhost:11434/api/chat",
            json={"message": {"content": json.dumps(response_data)}},
        )

        fp = FileProcessor(sentinel_config)
        fc = FileChange(path=source, change_type=Change.modified)
        try:
            result = await fp.generate_review(fc)
        finally:
            await fp.ollama_client.close()

        assert result["summary"] == "Found one issue."
        assert len(result["findings"]) == 1
        assert result["findings"][0]["verbatim_excerpt"] == "x = None"

    async def test_empty_findings_produces_prose_no_findings(
        self, sentinel_config, tmp_path, httpx_mock, caplog
    ):
        """A response with findings: [] persists no findings, writes prose, no warnings."""
        from ollama_sentinel.processor import FileProcessor, FileChange
        from watchfiles import Change
        import json

        source = tmp_path / "clean.py"
        source.write_text("print('hello')\n")

        response_data = {
            "summary": "Clean code, no issues.",
            "findings": [],
        }
        httpx_mock.add_response(
            url="http://localhost:11434/api/chat",
            json={"message": {"content": json.dumps(response_data)}},
        )

        fp = FileProcessor(sentinel_config)
        fc = FileChange(path=source, change_type=Change.modified)
        try:
            result = await fp.generate_review(fc)
        finally:
            await fp.ollama_client.close()

        assert result["summary"] == "Clean code, no issues."
        assert result["findings"] == []

        # No WARNING or ERROR logs
        for record in caplog.records:
            assert record.levelname not in ("WARNING", "ERROR")

    async def test_schema_parse_failure_falls_back_to_prose(
        self, sentinel_config, tmp_path, httpx_mock, caplog
    ):
        """A response that fails JSON parse falls back to prose with empty findings."""
        from ollama_sentinel.processor import FileProcessor, FileChange
        from watchfiles import Change

        source = tmp_path / "broken.py"
        source.write_text("x = 1\n")

        # Non-JSON response (e.g. model did not follow schema)
        httpx_mock.add_response(
            url="http://localhost:11434/api/chat",
            json={"message": {"content": "This is free-form prose review."}},
        )

        fp = FileProcessor(sentinel_config)
        fc = FileChange(path=source, change_type=Change.modified)
        try:
            result = await fp.generate_review(fc)
        finally:
            await fp.ollama_client.close()

        assert result["summary"] == "This is free-form prose review."
        assert result["findings"] == []
        # Parse failure is now a recoverable degrade, not an error.
        assert result["grounding_parse_failed"] is True

        warnings = [r.message for r in caplog.records if r.levelname == "WARNING"]
        assert any("review did not parse as json" in m.lower() for m in warnings)
        assert not [r for r in caplog.records if r.levelname == "ERROR"]

    async def test_valid_json_empty_findings_has_no_parse_failed_flag(
        self, sentinel_config, tmp_path, httpx_mock, caplog
    ):
        """Valid JSON with findings: [] must NOT set grounding_parse_failed."""
        from ollama_sentinel.processor import FileProcessor, FileChange
        from watchfiles import Change
        import json

        source = tmp_path / "clean.py"
        source.write_text("print('ok')\n")
        httpx_mock.add_response(
            url="http://localhost:11434/api/chat",
            json={"message": {"content": json.dumps(
                {"summary": "Clean.", "findings": []})}},
        )
        fp = FileProcessor(sentinel_config)
        fc = FileChange(path=source, change_type=Change.modified)
        try:
            result = await fp.generate_review(fc)
        finally:
            await fp.ollama_client.close()

        assert result["findings"] == []
        assert "grounding_parse_failed" not in result
        assert not [r for r in caplog.records
                    if r.levelname in ("WARNING", "ERROR")]

    async def test_schema_partial_json_flags_parse_failed(
        self, sentinel_config, tmp_path, httpx_mock
    ):
        """Valid JSON that ignores the schema (summary but no findings array)
        must degrade — this is the exact shape deepseek-v4-pro:cloud emits."""
        from ollama_sentinel.processor import FileProcessor, FileChange
        from watchfiles import Change
        import json

        source = tmp_path / "p.py"
        source.write_text("x = 1\n")
        httpx_mock.add_response(
            url="http://localhost:11434/api/chat",
            json={"message": {"content": json.dumps(
                {"summary": "No bugs found.",
                 "details": "x", "recommendations": []})}},
        )
        fp = FileProcessor(sentinel_config)
        fc = FileChange(path=source, change_type=Change.modified)
        try:
            result = await fp.generate_review(fc)
        finally:
            await fp.ollama_client.close()

        assert result["summary"] == "No bugs found."
        assert result["findings"] == []
        assert result["grounding_parse_failed"] is True

    async def test_non_dict_json_flags_parse_failed(
        self, sentinel_config, tmp_path, httpx_mock
    ):
        """Valid JSON that is not a dict (e.g. a bare array) must degrade."""
        from ollama_sentinel.processor import FileProcessor, FileChange
        from watchfiles import Change

        source = tmp_path / "q.py"
        source.write_text("y = 2\n")
        httpx_mock.add_response(
            url="http://localhost:11434/api/chat",
            json={"message": {"content": "[]"}},
        )
        fp = FileProcessor(sentinel_config)
        fc = FileChange(path=source, change_type=Change.modified)
        try:
            result = await fp.generate_review(fc)
        finally:
            await fp.ollama_client.close()

        assert result["findings"] == []
        assert result["grounding_parse_failed"] is True


# ---------------------------------------------------------------------------
# Recipe instruction ordering test (Step 2)
# ---------------------------------------------------------------------------


class TestRecipeInstructionOrdering:
    """Verify the INSTRUCTIONS section appears before the FILE section."""

    async def test_instructions_before_file_in_prompt(self, sentinel_config, tmp_path):
        """The INSTRUCTIONS block appears before the FILE block in the rendered prompt."""
        from ollama_sentinel.processor import FileProcessor, FileChange
        from watchfiles import Change

        source = tmp_path / "sample.py"
        source.write_text("x = 1\n")

        fp = FileProcessor(sentinel_config)
        fc = FileChange(path=source, change_type=Change.modified, content="x = 1\n")

        prompt = await fp.format_prompt(fc)
        instructions_pos = prompt.find("For each issue you flag, provide")
        file_pos = prompt.find("FILE:")
        assert instructions_pos != -1, "INSTRUCTIONS section not found in prompt"
        assert file_pos != -1, "FILE section not found in prompt"
        assert instructions_pos < file_pos, (
            "INSTRUCTIONS section must appear before FILE section"
        )

    async def test_instructions_omitted_when_grounding_disabled(self, sentinel_config, tmp_path):
        """With grounding off, the INSTRUCTIONS section is dropped from the prompt.

        The instruction references "the schema will reject findings without them",
        which is a lie when no schema is enforced. Better to drop it entirely than
        confuse the model with a fake constraint.
        """
        from ollama_sentinel.processor import FileProcessor, FileChange
        from watchfiles import Change

        source = tmp_path / "sample.py"
        source.write_text("x = 1\n")

        sentinel_config.processing.grounding = False
        fp = FileProcessor(sentinel_config)
        fc = FileChange(path=source, change_type=Change.modified, content="x = 1\n")

        prompt = await fp.format_prompt(fc)
        assert "For each issue you flag, provide" not in prompt
        assert "schema will reject" not in prompt
        assert "FILE:" in prompt  # File section still present


# ---------------------------------------------------------------------------
# Fixtures needed by the test classes above
# ---------------------------------------------------------------------------


@pytest.fixture
def sentinel_config(tmp_path):
    """Minimal SentinelConfig for FileProcessor tests."""
    from ollama_sentinel.models import (
        SentinelConfig,
        OllamaConfig,
        OllamaModelConfig,
        WatchConfig,
    )
    return SentinelConfig(
        watch=WatchConfig(directory=str(tmp_path)),
        ollama=OllamaConfig(
            host="http://localhost:11434",
            models={
                "default": OllamaModelConfig(
                    name="test-model", system_prompt="Review code."
                )
            },
        ),
    )
