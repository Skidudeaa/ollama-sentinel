"""Tests for OllamaClient and FileProcessor."""
import datetime
import json
import pathlib
from unittest.mock import patch

import httpx
import pytest
from pytest_httpx import HTTPXMock
from tenacity import wait_none
from watchfiles import Change

from ollama_sentinel.config import create_default_config, load_config
from ollama_sentinel.models import (
    HistoryConfig,
    MemoryConfig,
    OllamaConfig,
    OllamaModelConfig,
    OutputConfig,
    OutputFormat,
    SentinelConfig,
    WatchConfig,
    ProcessingConfig,
)
from ollama_sentinel.processor import FileChange, FileProcessor, OllamaClient


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
            "security": OllamaModelConfig(
                name="sec-model", system_prompt="Security review."
            ),
        },
    ).model_dump()


@pytest.fixture
def sentinel_config(tmp_path):
    """Minimal SentinelConfig for FileProcessor tests."""
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


@pytest.fixture
def small_chunk_config(tmp_path):
    """SentinelConfig with a very small chunk size to force multi-chunk splitting."""
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
        processing=ProcessingConfig(
            max_chars_per_chunk=50,
            overlap_chars=0,
            max_concurrent_chunks_per_file=2,
        ),
    )


# ---------------------------------------------------------------------------
# OllamaClient tests
# ---------------------------------------------------------------------------


class TestOllamaClient:
    """Tests for OllamaClient."""

    async def test_successful_review(self, ollama_config, httpx_mock: HTTPXMock):
        """Successful POST returns the message content from the JSON response."""
        httpx_mock.add_response(
            url=OLLAMA_CHAT_URL,
            json={"message": {"content": "Looks good!"}},
        )

        client = OllamaClient(ollama_config)
        try:
            result = await client.generate_review("default", "review this")
        finally:
            await client.close()

        assert result == "Looks good!"
        requests = httpx_mock.get_requests()
        assert len(requests) == 1
        payload = requests[0].read()
        assert b"test-model" in payload

    async def test_missing_role_falls_back_to_default(
        self, ollama_config, httpx_mock: HTTPXMock
    ):
        """An unknown model role falls back to the 'default' role."""
        httpx_mock.add_response(
            url=OLLAMA_CHAT_URL,
            json={"message": {"content": "default review"}},
        )

        client = OllamaClient(ollama_config)
        try:
            result = await client.generate_review("nonexistent_role", "review this")
        finally:
            await client.close()

        assert result == "default review"
        payload = httpx_mock.get_requests()[0].read()
        assert b"test-model" in payload

    async def test_malformed_json_raises_key_error(
        self, ollama_config, httpx_mock: HTTPXMock
    ):
        """A 200 response missing the 'message' key raises KeyError (not retried)."""
        httpx_mock.add_response(
            url=OLLAMA_CHAT_URL,
            json={"unexpected": "data"},
        )

        client = OllamaClient(ollama_config)
        try:
            with pytest.raises(KeyError):
                await client.generate_review("default", "review this")
        finally:
            await client.close()

        # Should have been called exactly once (no retries for KeyError).
        assert len(httpx_mock.get_requests()) == 1

    async def test_output_reserve_caps_generation_by_default(
        self, ollama_config, httpx_mock: HTTPXMock
    ):
        """Configured output reserve is sent to Ollama as num_predict."""
        httpx_mock.add_response(
            url=OLLAMA_CHAT_URL,
            json={"message": {"content": "bounded review"}},
        )

        client = OllamaClient(ollama_config)
        try:
            result = await client.generate_review("default", "review this")
        finally:
            await client.close()

        assert result == "bounded review"
        body = json.loads(httpx_mock.get_requests()[0].content)
        assert body["options"]["num_predict"] == 2000

    async def test_max_tokens_overrides_output_reserve(
        self, ollama_config, httpx_mock: HTTPXMock
    ):
        """Explicit max_tokens wins over the prompt-budget reserve."""
        httpx_mock.add_response(
            url=OLLAMA_CHAT_URL,
            json={"message": {"content": "bounded review"}},
        )

        client = OllamaClient(ollama_config)
        try:
            await client.generate_with_model(
                {
                    "name": "test-model",
                    "system_prompt": "Review code.",
                    "max_tokens": 321,
                    "output_reserve_tokens": 2000,
                },
                "review this",
            )
        finally:
            await client.close()

        body = json.loads(httpx_mock.get_requests()[0].content)
        assert body["options"]["num_predict"] == 321

    async def test_think_flag_is_forwarded_when_configured(
        self, ollama_config, httpx_mock: HTTPXMock
    ):
        """Thinking models can be forced into non-thinking chat mode."""
        httpx_mock.add_response(
            url=OLLAMA_CHAT_URL,
            json={"message": {"content": "review"}},
        )

        client = OllamaClient(ollama_config)
        try:
            await client.generate_with_model(
                {
                    "name": "test-model",
                    "system_prompt": "Review code.",
                    "think": False,
                },
                "review this",
            )
        finally:
            await client.close()

        body = json.loads(httpx_mock.get_requests()[0].content)
        assert body["think"] is False

    async def test_http_500_is_retried(self, ollama_config, httpx_mock: HTTPXMock):
        """HTTP 500 triggers tenacity retry; succeeds on the third attempt."""
        # Two 500 errors followed by a success.
        httpx_mock.add_response(url=OLLAMA_CHAT_URL, status_code=500)
        httpx_mock.add_response(url=OLLAMA_CHAT_URL, status_code=500)
        httpx_mock.add_response(
            url=OLLAMA_CHAT_URL,
            json={"message": {"content": "recovered"}},
        )

        client = OllamaClient(ollama_config)
        # Disable wait between retries so the test is fast.
        # The @retry decorator is on generate_with_model (the HTTP layer).
        client.generate_with_model.retry.wait = wait_none()
        try:
            result = await client.generate_review("default", "review this")
        finally:
            await client.close()

        assert result == "recovered"
        assert len(httpx_mock.get_requests()) == 3

    async def test_http_404_is_not_retried(self, ollama_config, httpx_mock: HTTPXMock):
        """Model/config errors should fail once rather than retrying."""
        httpx_mock.add_response(
            url=OLLAMA_CHAT_URL,
            status_code=404,
            json={"error": "model not found"},
        )

        client = OllamaClient(ollama_config)
        try:
            with pytest.raises(httpx.HTTPStatusError):
                await client.generate_review("default", "review this")
        finally:
            await client.close()

        assert len(httpx_mock.get_requests()) == 1

    async def test_read_timeout_is_not_retried(
        self, ollama_config, httpx_mock: HTTPXMock
    ):
        """Long model generations should not be repeated after a read timeout."""
        httpx_mock.add_exception(httpx.ReadTimeout("timed out"), url=OLLAMA_CHAT_URL)

        client = OllamaClient(ollama_config)
        try:
            with pytest.raises(httpx.ReadTimeout):
                await client.generate_review("default", "review this")
        finally:
            await client.close()

        assert len(httpx_mock.get_requests()) == 1

    async def test_valid_json_extracts_content(
        self, ollama_config, httpx_mock: HTTPXMock
    ):
        """Verify the exact content string is extracted from a well-formed response."""
        expected = "Line-by-line analysis:\n1. All good.\n2. Minor style issue."
        httpx_mock.add_response(
            url=OLLAMA_CHAT_URL,
            json={"message": {"content": expected}},
        )

        client = OllamaClient(ollama_config)
        try:
            result = await client.generate_review("default", "prompt")
        finally:
            await client.close()

        assert result == expected


# ---------------------------------------------------------------------------
# FileProcessor.format_prompt tests
# ---------------------------------------------------------------------------


class TestFormatPrompt:
    """Tests for FileProcessor.format_prompt."""

    async def test_chunk_text_used_directly(self, sentinel_config, tmp_path):
        """When chunk_text is supplied, it appears verbatim in the prompt."""
        fp = FileProcessor(sentinel_config)
        fc = FileChange(
            path=tmp_path / "app.py",
            change_type=Change.modified,
            content="full content that should be ignored",
        )

        prompt = await fp.format_prompt(fc, chunk_text="only this chunk")
        assert "only this chunk" in prompt
        assert "full content that should be ignored" not in prompt

    async def test_diff_format(self, sentinel_config, tmp_path):
        """When the file change has a diff, the prompt uses diff format."""
        fp = FileProcessor(sentinel_config)
        fc = FileChange(
            path=tmp_path / "app.py",
            change_type=Change.modified,
            diff="--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-old\n+new",
        )

        prompt = await fp.format_prompt(fc)
        assert "```diff" in prompt
        assert "-old" in prompt

    async def test_empty_content(self, sentinel_config, tmp_path):
        """Empty content produces the '<Empty File>' marker."""
        fp = FileProcessor(sentinel_config)
        fc = FileChange(
            path=tmp_path / "empty.py",
            change_type=Change.modified,
            content="",
        )

        prompt = await fp.format_prompt(fc)
        assert "<Empty File>" in prompt

    async def test_multi_chunk_includes_part_info(self, sentinel_config, tmp_path):
        """Multi-chunk prompts include 'Part X/Y' in the header."""
        fp = FileProcessor(sentinel_config)
        fc = FileChange(
            path=tmp_path / "big.py",
            change_type=Change.modified,
            content="some code",
        )

        prompt = await fp.format_prompt(fc, chunk_text="chunk text", chunk_index=2, total_chunks=5)
        assert "(Part 3/5)" in prompt


# ---------------------------------------------------------------------------
# FileProcessor.generate_review tests
# ---------------------------------------------------------------------------


class TestFileProcessorGenerateReview:
    """Tests for FileProcessor.generate_review."""

    async def test_single_chunk_one_api_call(
        self, sentinel_config, tmp_path, httpx_mock: HTTPXMock
    ):
        """A small file that fits in one chunk triggers exactly one API call."""
        # Create a real file so prepare_file_content can read it.
        source = tmp_path / "small.py"
        source.write_text("print('hello')")

        httpx_mock.add_response(
            url=OLLAMA_CHAT_URL,
            json={"message": {"content": "Single chunk review."}},
        )

        fp = FileProcessor(sentinel_config)
        fc = FileChange(path=source, change_type=Change.modified)
        try:
            result = await fp.generate_review(fc)
        finally:
            await fp.ollama_client.close()

        assert result == "Single chunk review."
        assert len(httpx_mock.get_requests()) == 1

    @pytest.mark.httpx_mock(assert_all_responses_were_requested=False)
    async def test_multi_chunk_multiple_api_calls(
        self, small_chunk_config, tmp_path, httpx_mock: HTTPXMock
    ):
        """Content exceeding the token budget triggers multiple API calls
        and the results are combined."""
        source = tmp_path / "big.py"
        # Create content large enough to require multiple chunks.
        lines = [f"line_{i} = {i}" for i in range(20)]
        source.write_text("\n".join(lines))

        # Register more responses than needed; the marker above permits leftovers.
        for i in range(10):
            httpx_mock.add_response(
                url=OLLAMA_CHAT_URL,
                json={"message": {"content": f"Review chunk {i}"}},
            )

        fp = FileProcessor(small_chunk_config)
        # The new chunker is token-based; the max(256, ...) floor in chunk_content
        # prevents very small budgets via total_budget alone. Monkeypatch
        # chunk_content to use a small per-chunk token limit (~15 tokens gives
        # ~3 chunks from 20 lines of "line_N = N") so we stay well under 10
        # registered mock responses.
        from ollama_sentinel.context.assembler import chunk_by_lines
        fp.chunk_content = lambda content, file_type: chunk_by_lines(
            content,
            counter=fp.counter,
            max_tokens=15,  # ~3–5 lines per chunk, produces ~5–7 chunks from 20 lines
            overlap_tokens=0,
        )

        fc = FileChange(path=source, change_type=Change.modified)
        try:
            result = await fp.generate_review(fc)
        finally:
            await fp.ollama_client.close()

        request_count = len(httpx_mock.get_requests())
        assert request_count > 1, "Expected multiple API calls for multi-chunk content"

        # The combined review should contain the header and part markers.
        assert "Combined Review" in result
        assert "## Part 1/" in result
        assert "## Part 2/" in result


# ---------------------------------------------------------------------------
# FileProcessor.save_review tests
# ---------------------------------------------------------------------------


def _make_sentinel_config(tmp_path, *, history_enabled=True, max_versions=3,
                          output_format=OutputFormat.MARKDOWN, compress=False,
                          diff_based_history=False):
    """Helper to build a SentinelConfig with controllable output settings."""
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
        output=OutputConfig(
            directory=".ollama_reviews",
            format=output_format,
            console_output=False,
            compress=compress,
            diff_based_history=diff_based_history,
            history=HistoryConfig(enabled=history_enabled, max_versions=max_versions),
        ),
    )


def _make_file_change(tmp_path, rel="src/app.py"):
    """Create a real file in tmp_path and return a FileChange pointing to it."""
    file_path = tmp_path / rel
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text("print('hello')")
    return FileChange(path=file_path, change_type=Change.modified)


class TestSaveReview:
    """Tests for FileProcessor.save_review."""

    def test_save_markdown_creates_versioned_and_latest(self, tmp_path):
        """Saving a markdown review creates both a versioned file and a latest file."""
        cfg = _make_sentinel_config(tmp_path)
        fp = FileProcessor(cfg)
        fc = _make_file_change(tmp_path)

        fake_now = datetime.datetime(2025, 3, 15, 10, 30, 45)
        with patch("ollama_sentinel.processor.datetime") as mock_dt:
            mock_dt.datetime.now.return_value = fake_now
            result_path = fp.save_review(fc, "Great code!")

        output_dir = tmp_path / ".ollama_reviews" / "src"
        # Versioned file
        versioned = output_dir / "app_20250315103045.md"
        assert versioned.exists()
        assert versioned.read_text() == "Great code!"
        assert result_path == versioned

        # Latest file
        latest = output_dir / "app.md"
        assert latest.exists()
        assert latest.read_text() == "Great code!"

    def test_multiple_saves_create_multiple_versioned_files(self, tmp_path):
        """Saving reviews at different timestamps creates distinct versioned files."""
        cfg = _make_sentinel_config(tmp_path, max_versions=10)
        fp = FileProcessor(cfg)
        fc = _make_file_change(tmp_path)

        timestamps = [
            datetime.datetime(2025, 1, 1, 0, 0, i) for i in range(1, 4)
        ]
        for ts in timestamps:
            with patch("ollama_sentinel.processor.datetime") as mock_dt:
                mock_dt.datetime.now.return_value = ts
                fp.save_review(fc, f"Review at {ts}")

        output_dir = tmp_path / ".ollama_reviews" / "src"
        versioned_files = sorted(
            p for p in output_dir.glob("app_*.md")
        )
        assert len(versioned_files) == 3

    def test_history_cleanup_removes_oldest(self, tmp_path):
        """When more than max_versions versioned files exist, the oldest are deleted."""
        cfg = _make_sentinel_config(tmp_path, max_versions=2)
        fp = FileProcessor(cfg)
        fc = _make_file_change(tmp_path)

        timestamps = [
            datetime.datetime(2025, 1, 1, 10, 0, i) for i in range(1, 5)
        ]
        for ts in timestamps:
            with patch("ollama_sentinel.processor.datetime") as mock_dt:
                mock_dt.datetime.now.return_value = ts
                fp.save_review(fc, f"Review at {ts}")

        output_dir = tmp_path / ".ollama_reviews" / "src"
        versioned_files = sorted(output_dir.glob("app_*.md"))
        # Only the newest 2 should survive
        assert len(versioned_files) == 2
        names = [p.name for p in versioned_files]
        assert "app_20250101100001.md" not in names
        assert "app_20250101100002.md" not in names
        assert "app_20250101100003.md" in names
        assert "app_20250101100004.md" in names

    def test_json_format_output(self, tmp_path):
        """JSON format wraps the review in a JSON object with expected keys."""
        cfg = _make_sentinel_config(tmp_path, output_format=OutputFormat.JSON)
        fp = FileProcessor(cfg)
        fc = _make_file_change(tmp_path)

        fake_now = datetime.datetime(2025, 6, 1, 12, 0, 0)
        with patch("ollama_sentinel.processor.datetime") as mock_dt:
            mock_dt.datetime.now.return_value = fake_now
            fp.save_review(fc, "JSON review content")

        output_dir = tmp_path / ".ollama_reviews" / "src"
        latest = output_dir / "app.json"
        assert latest.exists()

        data = json.loads(latest.read_text())
        assert data["file"] == "src/app.py"
        assert data["timestamp"] == "20250601120000"
        assert data["review"] == "JSON review content"

    def test_save_without_history_only_latest(self, tmp_path):
        """With history disabled, only the latest file is written, no versioned files."""
        cfg = _make_sentinel_config(tmp_path, history_enabled=False)
        fp = FileProcessor(cfg)
        fc = _make_file_change(tmp_path)

        fp.save_review(fc, "No history review")

        output_dir = tmp_path / ".ollama_reviews" / "src"
        latest = output_dir / "app.md"
        assert latest.exists()
        assert latest.read_text() == "No history review"

        # No versioned files should exist
        versioned = list(output_dir.glob("app_*.md"))
        assert versioned == []

    def test_output_directory_created_automatically(self, tmp_path):
        """The output directory tree is created automatically even if it does not exist."""
        cfg = _make_sentinel_config(tmp_path)
        fp = FileProcessor(cfg)
        # Use a nested path to verify parents=True behavior
        fc = _make_file_change(tmp_path, rel="deep/nested/dir/module.py")

        fake_now = datetime.datetime(2025, 1, 1, 0, 0, 0)
        with patch("ollama_sentinel.processor.datetime") as mock_dt:
            mock_dt.datetime.now.return_value = fake_now
            result_path = fp.save_review(fc, "Nested review")

        assert result_path.exists()
        assert result_path.read_text() == "Nested review"
        # Verify the full directory structure was created
        expected_dir = tmp_path / ".ollama_reviews" / "deep" / "nested" / "dir"
        assert expected_dir.is_dir()


# ---------------------------------------------------------------------------
# Config loading tests
# ---------------------------------------------------------------------------


class TestConfigLoading:
    """Tests for ollama_sentinel.config functions."""

    def test_load_config_valid_yaml(self, config_yaml_path):
        """load_config with a valid YAML file returns a SentinelConfig."""
        result = load_config(config_yaml_path)
        assert result is not None
        assert isinstance(result, SentinelConfig)

    def test_load_config_missing_file(self, tmp_path):
        """load_config with a non-existent file returns None."""
        missing = tmp_path / "does_not_exist.yaml"
        result = load_config(missing)
        assert result is None

    def test_load_config_invalid_yaml(self, tmp_path):
        """load_config with malformed YAML returns None."""
        bad_yaml = tmp_path / "bad.yaml"
        bad_yaml.write_text("watch:\n  directory: [unterminated")
        result = load_config(bad_yaml)
        assert result is None

    def test_create_default_config_has_expected_keys(self):
        """create_default_config returns a dict with all top-level config sections."""
        result = create_default_config("/some/dir")
        assert isinstance(result, dict)
        for key in ("watch", "ollama", "processing", "output"):
            assert key in result, f"Missing top-level key: {key}"

    def test_create_default_config_default_model_is_gemma(self):
        """The default model in create_default_config is gemma3:4b."""
        result = create_default_config("/some/dir")
        default_model = result["ollama"]["models"]["default"]["name"]
        assert default_model == "gemma3:4b"

    def test_create_default_config_uses_safe_local_ollama_defaults(self):
        """Generated configs avoid local Ollama overload and generated artifacts."""
        from ollama_sentinel.watcher import _BUILTIN_IGNORE_PATTERNS
        result = create_default_config("/some/dir")
        assert result["ollama"]["request_timeout"] == 180
        assert result["processing"]["max_concurrent_reviews"] == 1
        assert result["processing"]["max_concurrent_chunks_per_file"] == 1
        # .mdb and other binary extensions are covered by built-in patterns,
        # not by the init template — verify built-ins include them instead.
        assert "**/*.mdb" in _BUILTIN_IGNORE_PATTERNS
        assert result["watch"]["disable_builtin_ignores"] is False


# ---------------------------------------------------------------------------
# OllamaClient.generate_with_model tests
# ---------------------------------------------------------------------------


class TestGenerateWithModel:
    """Tests for OllamaClient.generate_with_model."""

    async def test_generate_with_explicit_config(self, ollama_config, httpx_mock):
        """Passing an explicit model dict bypasses the role lookup."""
        httpx_mock.add_response(
            url=OLLAMA_CHAT_URL,
            json={"message": {"content": "explicit output"}},
        )
        client = OllamaClient(ollama_config)
        try:
            out = await client.generate_with_model(
                {
                    "name": "ad-hoc-model",
                    "system_prompt": "You are ad-hoc.",
                    "temperature": 0.1,
                    "top_p": 0.9,
                },
                "a prompt",
            )
        finally:
            await client.close()
        assert out == "explicit output"
        # Body was built from the explicit config, not the role lookup.
        req = httpx_mock.get_requests()[0]
        import json as _json
        body = _json.loads(req.content)
        assert body["model"] == "ad-hoc-model"
        assert body["messages"][0]["content"] == "You are ad-hoc."

    async def test_generate_review_still_uses_role_lookup(self, ollama_config, httpx_mock):
        """The existing generate_review(role, prompt) path remains."""
        httpx_mock.add_response(
            url=OLLAMA_CHAT_URL,
            json={"message": {"content": "default output"}},
        )
        client = OllamaClient(ollama_config)
        try:
            out = await client.generate_review("default", "a prompt")
        finally:
            await client.close()
        assert out == "default output"

    async def test_generate_review_can_request_json_format(self, ollama_config, httpx_mock):
        """Passing response_format sends Ollama's structured-output format flag."""
        httpx_mock.add_response(
            url=OLLAMA_CHAT_URL,
            json={"message": {"content": "[]"}},
        )
        client = OllamaClient(ollama_config)
        try:
            out = await client.generate_review(
                "default",
                "extract findings",
                response_format="json",
            )
        finally:
            await client.close()
        assert out == "[]"
        import json as _json
        body = _json.loads(httpx_mock.get_requests()[0].content)
        assert body["format"] == "json"
        assert body["options"]["temperature"] == 0.1

    async def test_generate_with_model_config_object(self, ollama_config, httpx_mock):
        """Passing an OllamaModelConfig object (not dict) also works."""
        httpx_mock.add_response(
            url=OLLAMA_CHAT_URL,
            json={"message": {"content": "obj output"}},
        )
        from ollama_sentinel.models import OllamaModelConfig
        mc = OllamaModelConfig(name="obj-model", system_prompt="obj prompt")
        client = OllamaClient(ollama_config)
        try:
            out = await client.generate_with_model(mc, "prompt")
        finally:
            await client.close()
        assert out == "obj output"
        import json as _json
        body = _json.loads(httpx_mock.get_requests()[0].content)
        assert body["model"] == "obj-model"
        assert body["messages"][0]["content"] == "obj prompt"


# ---------------------------------------------------------------------------
# Structural recall (import-graph neighbor recall) tests
# ---------------------------------------------------------------------------


class TestStructuralRecall:
    """Tests for the import-graph fallback layer in _get_ranked_prior_violations."""

    @staticmethod
    def _config(tmp_path, *, structural_recall=True):
        """Config with semantic recall disabled to deterministically exercise
        the structural / single-file paths without an Ollama dependency."""
        return SentinelConfig(
            watch=WatchConfig(directory=str(tmp_path)),
            ollama=OllamaConfig(
                host="http://localhost:11434",
                models={
                    "default": OllamaModelConfig(
                        name="test-model", system_prompt="Review code."
                    ),
                },
            ),
            memory=MemoryConfig(
                enabled=True,
                db_path=".ollama_reviews/memory.db",
                semantic_recall=False,
                structural_recall=structural_recall,
            ),
        )

    @staticmethod
    def _make_db(tmp_path):
        """Create a ViolationDB rooted in tmp_path."""
        from ollama_sentinel.violation_db import ViolationDB
        db_path = tmp_path / ".ollama_reviews" / "memory.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        return ViolationDB(str(db_path))

    async def test_resolves_findings_from_imported_file(self, tmp_path):
        """A finding on `utils.py` surfaces when reviewing `app.py` (which imports it)."""
        from ollama_sentinel.violation_db import Finding

        (tmp_path / "utils.py").write_text("def helper():\n    return 1\n")
        app = tmp_path / "app.py"
        app.write_text("from utils import helper\nprint(helper())\n")

        db = self._make_db(tmp_path)
        try:
            db.persist_findings("utils.py", [Finding(
                file_path="utils.py", line_start=1, line_end=2,
                category="bug", severity="high",
                description="helper returns wrong type",
            )])

            fp = FileProcessor(self._config(tmp_path), violation_db=db)
            try:
                violations = await fp._get_ranked_prior_violations(
                    app, file_content=app.read_text(),
                )
            finally:
                await fp.close()

            assert violations is not None
            paths = {v["file_path"] for v in violations}
            assert "utils.py" in paths
        finally:
            db.close()

    async def test_resolves_findings_from_dependent_file(self, tmp_path):
        """A finding on `app.py` surfaces when reviewing `utils.py` (which it imports)."""
        from ollama_sentinel.violation_db import Finding

        utils = tmp_path / "utils.py"
        utils.write_text("def helper():\n    return 1\n")
        (tmp_path / "app.py").write_text(
            "from utils import helper\nprint(helper())\n"
        )

        db = self._make_db(tmp_path)
        try:
            db.persist_findings("app.py", [Finding(
                file_path="app.py", line_start=1, line_end=1,
                category="security", severity="high",
                description="unvalidated input",
            )])

            fp = FileProcessor(self._config(tmp_path), violation_db=db)
            try:
                violations = await fp._get_ranked_prior_violations(
                    utils, file_content=utils.read_text(),
                )
            finally:
                await fp.close()

            assert violations is not None
            paths = {v["file_path"] for v in violations}
            assert "app.py" in paths
        finally:
            db.close()

    async def test_falls_back_to_single_file_for_non_python(self, tmp_path):
        """Non-Python files bypass Layer 2 — the resolver is Python-only."""
        from ollama_sentinel.violation_db import Finding

        target = tmp_path / "page.html"
        target.write_text("<html></html>\n")
        # Unrelated Python file with a finding to verify it does NOT leak in.
        (tmp_path / "other.py").write_text("x = 1\n")

        db = self._make_db(tmp_path)
        try:
            db.persist_findings("page.html", [Finding(
                file_path="page.html", line_start=1, line_end=1,
                category="style", severity="low",
                description="missing doctype",
            )])
            db.persist_findings("other.py", [Finding(
                file_path="other.py", line_start=1, line_end=1,
                category="bug", severity="high",
                description="should not surface for an HTML file",
            )])

            fp = FileProcessor(self._config(tmp_path), violation_db=db)
            try:
                violations = await fp._get_ranked_prior_violations(
                    target, file_content=target.read_text(),
                )
            finally:
                await fp.close()

            assert violations is not None
            paths = {v["file_path"] for v in violations}
            assert paths == {"page.html"}
        finally:
            db.close()

    async def test_handles_syntax_error_gracefully(self, tmp_path):
        """A file with a SyntaxError still gets its own findings via Layer 2/3."""
        from ollama_sentinel.violation_db import Finding

        broken = tmp_path / "broken.py"
        broken.write_text("def f(:\n  pass\n")  # intentional syntax error

        db = self._make_db(tmp_path)
        try:
            db.persist_findings("broken.py", [Finding(
                file_path="broken.py", line_start=1, line_end=1,
                category="bug", severity="high",
                description="prior finding from earlier review",
            )])

            fp = FileProcessor(self._config(tmp_path), violation_db=db)
            try:
                violations = await fp._get_ranked_prior_violations(
                    broken, file_content=broken.read_text(),
                )
            finally:
                await fp.close()

            assert violations is not None
            paths = {v["file_path"] for v in violations}
            assert "broken.py" in paths
        finally:
            db.close()

    async def test_disabled_via_config_skips_neighbor_lookup(self, tmp_path):
        """structural_recall: false suppresses Layer 2 entirely."""
        from ollama_sentinel.violation_db import Finding

        (tmp_path / "utils.py").write_text("def helper():\n    return 1\n")
        app = tmp_path / "app.py"
        app.write_text("from utils import helper\nprint(helper())\n")

        db = self._make_db(tmp_path)
        try:
            # Finding only on utils.py — only structural recall would surface
            # it when reviewing app.py. With structural disabled and no
            # findings on app.py itself, the result must be None.
            db.persist_findings("utils.py", [Finding(
                file_path="utils.py", line_start=1, line_end=2,
                category="bug", severity="high",
                description="never surfaces",
            )])

            fp = FileProcessor(
                self._config(tmp_path, structural_recall=False), violation_db=db,
            )
            try:
                violations = await fp._get_ranked_prior_violations(
                    app, file_content=app.read_text(),
                )
            finally:
                await fp.close()

            assert violations is None
        finally:
            db.close()
