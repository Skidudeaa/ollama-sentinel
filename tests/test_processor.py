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
        client.generate_review.retry.wait = wait_none()
        try:
            result = await client.generate_review("default", "review this")
        finally:
            await client.close()

        assert result == "recovered"
        assert len(httpx_mock.get_requests()) == 3

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

    def test_chunk_text_used_directly(self, sentinel_config, tmp_path):
        """When chunk_text is supplied, it appears verbatim in the prompt."""
        fp = FileProcessor(sentinel_config)
        fc = FileChange(
            path=tmp_path / "app.py",
            change_type=Change.modified,
            content="full content that should be ignored",
        )

        prompt = fp.format_prompt(fc, chunk_text="only this chunk")
        assert "only this chunk" in prompt
        assert "full content that should be ignored" not in prompt

    def test_diff_format(self, sentinel_config, tmp_path):
        """When the file change has a diff, the prompt uses diff format."""
        fp = FileProcessor(sentinel_config)
        fc = FileChange(
            path=tmp_path / "app.py",
            change_type=Change.modified,
            diff="--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-old\n+new",
        )

        prompt = fp.format_prompt(fc)
        assert "(Git Diff)" in prompt
        assert "```diff" in prompt
        assert "-old" in prompt

    def test_empty_content(self, sentinel_config, tmp_path):
        """Empty content produces the '<Empty File>' marker."""
        fp = FileProcessor(sentinel_config)
        fc = FileChange(
            path=tmp_path / "empty.py",
            change_type=Change.modified,
            content="",
        )

        prompt = fp.format_prompt(fc)
        assert "<Empty File>" in prompt

    def test_multi_chunk_includes_part_info(self, sentinel_config, tmp_path):
        """Multi-chunk prompts include 'Part X/Y' in the header."""
        fp = FileProcessor(sentinel_config)
        fc = FileChange(
            path=tmp_path / "big.py",
            change_type=Change.modified,
            content="some code",
        )

        prompt = fp.format_prompt(fc, chunk_text="chunk text", chunk_index=2, total_chunks=5)
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
        """Content exceeding max_chars_per_chunk triggers multiple API calls
        and the results are combined."""
        source = tmp_path / "big.py"
        # Create content large enough to require multiple chunks with the
        # small_chunk_config (max_chars_per_chunk=50).
        lines = [f"line_{i} = {i}" for i in range(20)]
        source.write_text("\n".join(lines))

        # Register more responses than needed; the marker above permits leftovers.
        for i in range(10):
            httpx_mock.add_response(
                url=OLLAMA_CHAT_URL,
                json={"message": {"content": f"Review chunk {i}"}},
            )

        fp = FileProcessor(small_chunk_config)
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
