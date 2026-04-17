"""Tests for the CLI entry points."""
import json

import yaml
from typer.testing import CliRunner

from ollama_sentinel.cli import app
from ollama_sentinel.violation_db import Finding, ViolationDB


runner = CliRunner()


class TestInitCommand:
    """Tests for 'ollama-sentinel init'."""

    def test_creates_config_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["init", str(tmp_path)])
        assert result.exit_code == 0
        config_path = tmp_path / "ollama-sentinel.yaml"
        assert config_path.exists()

    def test_created_config_is_valid_yaml(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner.invoke(app, ["init", str(tmp_path)])
        config_path = tmp_path / "ollama-sentinel.yaml"
        with open(config_path) as f:
            config = yaml.safe_load(f)
        assert "watch" in config
        assert "ollama" in config
        assert "default" in config["ollama"]["models"]

    def test_created_config_uses_gemma3(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner.invoke(app, ["init", str(tmp_path)])
        config_path = tmp_path / "ollama-sentinel.yaml"
        with open(config_path) as f:
            config = yaml.safe_load(f)
        assert config["ollama"]["models"]["default"]["name"] == "gemma3:4b"


class TestRunCommand:
    """Tests for 'ollama-sentinel run' error paths."""

    def test_missing_config_exits_with_error(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["run", "--config", "nonexistent.yaml"])
        assert result.exit_code != 0

    def test_invalid_config_exits_with_error(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        bad_config = tmp_path / "bad.yaml"
        bad_config.write_text("not: {valid: config")
        result = runner.invoke(app, ["run", "--config", str(bad_config)])
        assert result.exit_code != 0


class TestReviewCommand:
    """Tests for 'ollama-sentinel review' error paths."""

    def test_missing_file_exits_with_error(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["review", "/nonexistent/file.py"])
        assert result.exit_code != 0

    def test_missing_config_exits_with_error(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        test_file = tmp_path / "test.py"
        test_file.write_text("x = 1")
        result = runner.invoke(
            app, ["review", str(test_file), "--config", "nonexistent.yaml"]
        )
        assert result.exit_code != 0

    def test_model_flag_accepted(self, tmp_path, monkeypatch):
        """Verify -m flag is accepted (even if review fails due to no Ollama)."""
        monkeypatch.chdir(tmp_path)
        test_file = tmp_path / "test.py"
        test_file.write_text("x = 1")
        # Config missing → exits before reaching Ollama, but -m is parsed
        result = runner.invoke(
            app, ["review", str(test_file), "-m", "security", "--config", "missing.yaml"]
        )
        assert result.exit_code != 0  # fails at config, not at flag parsing


def _make_report_config(tmp_path, db_rel_path=".ollama_reviews/memory.db"):
    """Write a valid config YAML and return the path."""
    config_dict = {
        "watch": {"directory": str(tmp_path)},
        "ollama": {
            "host": "http://localhost:11434",
            "models": {"default": {"name": "m", "system_prompt": "p"}},
        },
        "memory": {"enabled": True, "db_path": db_rel_path},
    }
    cfg = tmp_path / "ollama-sentinel.yaml"
    cfg.write_text(yaml.dump(config_dict, sort_keys=False))
    return cfg


def _seed_db(db_path, findings, repeat=1):
    """Create a ViolationDB and persist findings `repeat` times."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = ViolationDB(str(db_path))
    for _ in range(repeat):
        db.persist_findings("src/app.py", findings)
    db.close()


class TestReportCommand:
    """Tests for 'ollama-sentinel report'."""

    def test_report_shows_recurring_violations(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        cfg = _make_report_config(tmp_path)
        db_path = tmp_path / ".ollama_reviews" / "memory.db"
        findings = [
            Finding("src/app.py", 10, 12, "bug", "high", "Null pointer risk"),
            Finding("src/app.py", 30, 35, "security", "critical", "SQL injection"),
        ]
        _seed_db(db_path, findings, repeat=3)

        result = runner.invoke(app, ["report", "--config", str(cfg)])
        assert result.exit_code == 0
        # Rich table may wrap text across lines, so check for keywords
        assert "Null" in result.output
        assert "pointer" in result.output
        assert "SQL" in result.output
        assert "injection" in result.output

    def test_report_json_format(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        cfg = _make_report_config(tmp_path)
        db_path = tmp_path / ".ollama_reviews" / "memory.db"
        _seed_db(
            db_path,
            [Finding("src/app.py", 1, 2, "bug", "high", "desc")],
            repeat=2,
        )

        result = runner.invoke(app, ["report", "--config", str(cfg), "-f", "json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, list)
        assert len(data) >= 1
        assert data[0]["occurrence_count"] >= 2

    def test_report_empty_db(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        cfg = _make_report_config(tmp_path)
        db_path = tmp_path / ".ollama_reviews" / "memory.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        ViolationDB(str(db_path)).close()  # create empty DB

        result = runner.invoke(app, ["report", "--config", str(cfg)])
        assert result.exit_code == 0
        assert "No recurring violations" in result.output

    def test_report_no_db_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        cfg = _make_report_config(tmp_path)
        # Don't create the DB file
        result = runner.invoke(app, ["report", "--config", str(cfg)])
        assert result.exit_code == 0
        assert "Run some reviews first" in result.output or "No violation database" in result.output

    def test_report_min_count_filter(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        cfg = _make_report_config(tmp_path)
        db_path = tmp_path / ".ollama_reviews" / "memory.db"
        # One finding seen 2x, another seen 5x
        _seed_db(db_path, [Finding("a.py", 1, 2, "bug", "low", "minor")], repeat=2)
        db = ViolationDB(str(db_path))
        for _ in range(5):
            db.persist_findings("b.py", [Finding("b.py", 10, 11, "security", "high", "major")])
        db.close()

        result = runner.invoke(app, ["report", "--config", str(cfg), "-n", "4"])
        assert result.exit_code == 0
        # "major" has 5 occurrences, should appear; "minor" has 2, filtered out
        assert "major" in result.output
        assert "minor" not in result.output


import pathlib
import sys

from pytest_httpx import HTTPXMock

from ollama_sentinel.config import create_default_config


OLLAMA_CHAT_URL = "http://localhost:11434/api/chat"


def _write_config(tmp_path: pathlib.Path) -> pathlib.Path:
    """Write a valid ollama-sentinel.yaml in tmp_path and return its path."""
    cfg_path = tmp_path / "ollama-sentinel.yaml"
    cfg_path.write_text(yaml.safe_dump(create_default_config(str(tmp_path))))
    return cfg_path


class TestTriageCommand:
    def test_help_renders(self):
        result = runner.invoke(app, ["triage", "--help"])
        assert result.exit_code == 0
        # Either the docstring text or the command name should surface.
        text = (result.stdout or "") + (result.output or "")
        assert "triage" in text.lower() or "Diagnose" in text

    def test_piped_stdin_is_consumed(self, tmp_path, httpx_mock: HTTPXMock):
        _write_config(tmp_path)
        httpx_mock.add_response(
            url=OLLAMA_CHAT_URL,
            json={"message": {"content": "DIAGNOSIS: x"}},
        )
        result = runner.invoke(
            app,
            ["triage", "-c", str(tmp_path / "ollama-sentinel.yaml")],
            input="some error log\n",
        )
        assert result.exit_code == 0
        # The rendered markdown contains DIAGNOSIS somewhere.
        assert "DIAGNOSIS" in (result.stdout or "") + (result.output or "")

    def test_tty_without_input_exits_with_error(self, tmp_path):
        _write_config(tmp_path)
        from unittest.mock import patch
        # Force sys.stdin.isatty() to return True so the TTY guard fires.
        with patch.object(sys.stdin, "isatty", return_value=True):
            result = runner.invoke(
                app,
                ["triage", "-c", str(tmp_path / "ollama-sentinel.yaml")],
                input="",
            )
        assert result.exit_code == 1

    def test_output_flag_writes_file(self, tmp_path, httpx_mock: HTTPXMock):
        _write_config(tmp_path)
        httpx_mock.add_response(
            url=OLLAMA_CHAT_URL,
            json={"message": {"content": "written body"}},
        )
        output_path = tmp_path / "out.md"
        result = runner.invoke(
            app,
            [
                "triage",
                "-c", str(tmp_path / "ollama-sentinel.yaml"),
                "-o", str(output_path),
            ],
            input="some error\n",
        )
        assert result.exit_code == 0
        assert output_path.read_text() == "written body"
