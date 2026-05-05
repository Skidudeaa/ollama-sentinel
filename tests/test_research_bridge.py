"""Tests for the research bridge module."""
import json
import time
from unittest.mock import patch

from ollama_sentinel.research_bridge import (
    is_available,
    load_latest,
    persist_latest,
)


class TestIsAvailable:
    def test_returns_bool(self):
        result = is_available()
        assert isinstance(result, bool)

    def test_returns_false_when_import_fails(self):
        with patch.dict("sys.modules", {"research_agent.core.agent": None}):
            with patch("builtins.__import__", side_effect=ImportError("no langchain")):
                assert is_available() is False


class TestPersistLatest:
    def test_writes_json_file(self, tmp_path):
        result = {
            "query": "test question",
            "answer": "test answer",
            "confidence": 0.85,
            "timestamp": time.time(),
            "duration_s": 5.2,
            "source_count": 3,
        }
        path = persist_latest(result, tmp_path)
        assert path.exists()
        assert path.name == "latest.json"
        data = json.loads(path.read_text())
        assert data["query"] == "test question"
        assert data["confidence"] == 0.85

    def test_creates_research_subdirectory(self, tmp_path):
        persist_latest({"query": "q"}, tmp_path)
        assert (tmp_path / "research").is_dir()

    def test_overwrites_existing(self, tmp_path):
        persist_latest({"query": "first"}, tmp_path)
        persist_latest({"query": "second"}, tmp_path)
        data = json.loads((tmp_path / "research" / "latest.json").read_text())
        assert data["query"] == "second"


class TestLoadLatest:
    def test_returns_none_when_no_file(self, tmp_path):
        assert load_latest(tmp_path) is None

    def test_returns_none_when_dir_missing(self, tmp_path):
        assert load_latest(tmp_path / "nonexistent") is None

    def test_roundtrip(self, tmp_path):
        original = {
            "query": "test",
            "answer": "result",
            "confidence": 0.9,
            "timestamp": 1700000000.0,
            "duration_s": 3.1,
            "source_count": 2,
        }
        persist_latest(original, tmp_path)
        loaded = load_latest(tmp_path)
        assert loaded == original

    def test_returns_none_on_corrupt_json(self, tmp_path):
        research_dir = tmp_path / "research"
        research_dir.mkdir()
        (research_dir / "latest.json").write_text("not json{{{")
        assert load_latest(tmp_path) is None


class TestResearchCLICommand:
    def test_research_help_renders(self):
        from typer.testing import CliRunner
        from ollama_sentinel.cli import app

        runner = CliRunner()
        result = runner.invoke(app, ["research", "--help"])
        assert result.exit_code == 0
        output = (result.stdout or "") + (result.output or "")
        assert "Research" in output or "research" in output

    def test_research_missing_extras_shows_install_hint(self):
        from typer.testing import CliRunner
        from ollama_sentinel.cli import app

        runner = CliRunner()
        with patch("ollama_sentinel.research_bridge.is_available", return_value=False):
            result = runner.invoke(app, ["research", "test query"])
        assert result.exit_code == 1
        output = (result.stdout or "") + (result.output or "")
        assert "pip install" in output
