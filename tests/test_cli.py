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

    def test_empty_input_exits_with_error(self, tmp_path, caplog):
        _write_config(tmp_path)
        with caplog.at_level("ERROR"):
            result = runner.invoke(
                app,
                ["triage", "-c", str(tmp_path / "ollama-sentinel.yaml")],
                input="",
            )
        assert result.exit_code == 1
        assert "Empty input" in caplog.text or "No input" in caplog.text

    def test_tty_branch_exits_with_no_input_message(self, tmp_path, monkeypatch, caplog):
        _write_config(tmp_path)
        monkeypatch.setattr("ollama_sentinel.cli._is_stdin_tty", lambda: True)
        with caplog.at_level("ERROR"):
            result = runner.invoke(
                app,
                ["triage", "-c", str(tmp_path / "ollama-sentinel.yaml")],
            )
        assert result.exit_code == 1
        assert "No input" in caplog.text

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

    def test_positional_input_file_is_consumed(self, tmp_path, httpx_mock: HTTPXMock):
        _write_config(tmp_path)
        log_file = tmp_path / "pytest.log"
        log_file.write_text("some error from a saved log\n")
        httpx_mock.add_response(
            url=OLLAMA_CHAT_URL,
            json={"message": {"content": "from-positional-input"}},
        )
        result = runner.invoke(
            app,
            [
                "triage",
                str(log_file),
                "-c", str(tmp_path / "ollama-sentinel.yaml"),
            ],
        )
        assert result.exit_code == 0
        assert "from-positional-input" in (result.stdout or "") + (result.output or "")

    def test_positional_input_nonexistent_exits_1(self, tmp_path):
        _write_config(tmp_path)
        result = runner.invoke(
            app,
            [
                "triage",
                str(tmp_path / "does-not-exist.log"),
                "-c", str(tmp_path / "ollama-sentinel.yaml"),
            ],
        )
        assert result.exit_code == 1

    def test_context_file_not_found_exits_1(self, tmp_path):
        _write_config(tmp_path)
        result = runner.invoke(
            app,
            [
                "triage",
                "-c", str(tmp_path / "ollama-sentinel.yaml"),
                "--context", str(tmp_path / "missing.py"),
            ],
            input="some error\n",
        )
        assert result.exit_code == 1


class TestDefaultCommand:
    """Tests for bare `ollama-sentinel` invocation (no subcommand)."""

    def test_bare_invocation_no_config_shows_help(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, [])
        assert result.exit_code == 0
        output = (result.stdout or "") + (result.output or "")
        assert "Usage" in output or "ollama-sentinel" in output.lower()

    def test_bare_invocation_with_config_launches_dashboard(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_config(tmp_path)
        from unittest.mock import patch as mock_patch
        with mock_patch("ollama_sentinel.dashboard.run_dashboard") as mock_run:
            with mock_patch("asyncio.run") as mock_asyncio_run:
                result = runner.invoke(app, [])
        assert mock_asyncio_run.called

    def test_version_flag_still_works(self):
        result = runner.invoke(app, ["--version"])
        assert result.exit_code == 0
        output = (result.stdout or "") + (result.output or "")
        assert "ollama-sentinel" in output


# ---------------------------------------------------------------------------
# v0.2 Piece 3 — `ollama-sentinel confirm`
# ---------------------------------------------------------------------------


def _seed_one_finding_id(db_path):
    """Seed a single finding and return (db_path, finding_id)."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = ViolationDB(str(db_path))
    try:
        db.persist_findings(
            "src/app.py",
            [Finding("src/app.py", 10, 12, "bug", "high", "Null deref")],
        )
        fid = db._conn.execute(
            "SELECT id FROM findings ORDER BY id DESC LIMIT 1"
        ).fetchone()[0]
    finally:
        db.close()
    return fid


class TestConfirmCommand:
    """Tests for 'ollama-sentinel confirm' (manual corroboration)."""

    def test_confirm_creates_incident(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        cfg = _make_report_config(tmp_path)
        db_path = tmp_path / ".ollama_reviews" / "memory.db"
        fid = _seed_one_finding_id(db_path)

        result = runner.invoke(
            app, ["confirm", str(fid), "--config", str(cfg)]
        )
        assert result.exit_code == 0, result.output

        db = ViolationDB(str(db_path))
        try:
            incidents = db.get_incidents_for_finding(fid)
            assert len(incidents) == 1
            assert incidents[0]["confirming_signal"] == "manual_confirm"
            # Confirmation is corroboration, NOT resolution — finding stays open.
            assert len(db.get_unresolved("src/app.py")) == 1
        finally:
            db.close()

    def test_confirm_nonexistent_finding_errors(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        cfg = _make_report_config(tmp_path)
        db_path = tmp_path / ".ollama_reviews" / "memory.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        ViolationDB(str(db_path)).close()  # empty DB, no finding 9999

        result = runner.invoke(
            app, ["confirm", "9999", "--config", str(cfg)]
        )
        assert result.exit_code == 1
        assert "9999" in result.output or "no finding" in result.output.lower()

    def test_confirm_with_note(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        cfg = _make_report_config(tmp_path)
        db_path = tmp_path / ".ollama_reviews" / "memory.db"
        fid = _seed_one_finding_id(db_path)

        result = runner.invoke(
            app,
            ["confirm", str(fid), "--config", str(cfg), "--note", "hit in prod"],
        )
        assert result.exit_code == 0, result.output

        db = ViolationDB(str(db_path))
        try:
            artifact = db.get_incidents_for_finding(fid)[0]["confirming_artifact"]
            assert "hit in prod" in artifact
        finally:
            db.close()
