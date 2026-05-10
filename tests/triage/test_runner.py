"""Tests for ollama_sentinel.triage.runner.run_triage."""
import json

import pytest
from pytest_httpx import HTTPXMock

from ollama_sentinel.models import (
    OllamaConfig, OllamaModelConfig, SentinelConfig, WatchConfig,
)
from ollama_sentinel.triage.prompts import TRIAGE_SYSTEM_PROMPT
from ollama_sentinel.triage.runner import run_triage


OLLAMA_CHAT_URL = "http://localhost:11434/api/chat"


def _sentinel_config(tmp_path, models=None):
    if models is None:
        models = {"default": OllamaModelConfig(name="d", system_prompt="Default.")}
    return SentinelConfig(
        watch=WatchConfig(directory=str(tmp_path)),
        ollama=OllamaConfig(host="http://localhost:11434", models=models),
    )


class TestRunTriage:
    async def test_happy_path(self, tmp_path, httpx_mock: HTTPXMock):
        (tmp_path / "foo.py").write_text("x = 1\n" * 20)
        httpx_mock.add_response(
            url=OLLAMA_CHAT_URL,
            json={"message": {"content": "DIAGNOSIS: bad\nFIX: good\nCONFIDENCE: high"}},
        )
        cfg = _sentinel_config(tmp_path)

        out = await run_triage(
            input_text='File "foo.py", line 5, in main\nNameError: x',
            config=cfg,
            cwd=tmp_path,
        )
        assert "DIAGNOSIS" in out and "FIX" in out

    async def test_triage_role_missing_triggers_fallback(
        self, tmp_path, httpx_mock: HTTPXMock
    ):
        """When config has no 'triage' role, use the built-in system prompt."""
        httpx_mock.add_response(
            url=OLLAMA_CHAT_URL,
            json={"message": {"content": "ok"}},
        )
        cfg = _sentinel_config(
            tmp_path,
            models={
                "default": OllamaModelConfig(
                    name="d", system_prompt="Default.", think=False,
                )
            },
        )

        await run_triage(
            input_text="some error",
            config=cfg,
            cwd=tmp_path,
        )

        req = httpx_mock.get_requests()[0]
        body = json.loads(req.content)
        assert body["messages"][0]["content"] == TRIAGE_SYSTEM_PROMPT
        assert body["model"] == "d"
        assert body["think"] is False

    async def test_explicit_triage_role_is_used(
        self, tmp_path, httpx_mock: HTTPXMock
    ):
        """When config has a 'triage' role, its prompt and model win."""
        httpx_mock.add_response(
            url=OLLAMA_CHAT_URL,
            json={"message": {"content": "ok"}},
        )
        cfg = _sentinel_config(
            tmp_path,
            models={
                "default": OllamaModelConfig(name="d", system_prompt="Default."),
                "triage": OllamaModelConfig(name="t", system_prompt="Triage-custom."),
            },
        )
        await run_triage(input_text="some error", config=cfg, cwd=tmp_path)
        req = httpx_mock.get_requests()[0]
        body = json.loads(req.content)
        assert body["model"] == "t"
        assert body["messages"][0]["content"] == "Triage-custom."

    async def test_unknown_role_raises(self, tmp_path):
        """User-passed role that doesn't exist and isn't 'triage' must error."""
        cfg = _sentinel_config(tmp_path)
        with pytest.raises(KeyError):
            await run_triage(
                input_text="x",
                config=cfg,
                cwd=tmp_path,
                model_role="nonexistent",
            )

    async def test_no_extract_skips_auto_extraction(
        self, tmp_path, httpx_mock: HTTPXMock
    ):
        """With extract=False, referenced paths in the input are ignored."""
        (tmp_path / "foo.py").write_text("x\n" * 20)
        httpx_mock.add_response(
            url=OLLAMA_CHAT_URL,
            json={"message": {"content": "ok"}},
        )
        cfg = _sentinel_config(tmp_path)
        await run_triage(
            input_text='File "foo.py", line 5, in main',
            config=cfg,
            cwd=tmp_path,
            extract=False,
        )
        req = httpx_mock.get_requests()[0]
        body = json.loads(req.content)
        assert "REFERENCED SOURCE" not in body["messages"][1]["content"]

    async def test_explicit_context_file_included(
        self, tmp_path, httpx_mock: HTTPXMock
    ):
        f = tmp_path / "ctx.py"
        f.write_text("relevant code\n")
        httpx_mock.add_response(
            url=OLLAMA_CHAT_URL,
            json={"message": {"content": "ok"}},
        )
        cfg = _sentinel_config(tmp_path)
        await run_triage(
            input_text="error",
            config=cfg,
            cwd=tmp_path,
            explicit_context=[f],
            extract=False,
        )
        req = httpx_mock.get_requests()[0]
        body = json.loads(req.content)
        assert "USER-PROVIDED CONTEXT" in body["messages"][1]["content"]
        assert "relevant code" in body["messages"][1]["content"]
