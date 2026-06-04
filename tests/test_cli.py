"""Tests for the CLI entry points."""
import json

import yaml
from typer.testing import CliRunner

from ollama_sentinel.cli import app
from ollama_sentinel.violation_db import Finding, Incident, ViolationDB


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
                # asyncio.run is mocked, so the coroutine that run_dashboard (an
                # AsyncMock) returns would otherwise be garbage-collected
                # un-awaited and emit a RuntimeWarning that pytest mis-attributes
                # to a later test. Consume it here.
                mock_asyncio_run.side_effect = lambda coro: coro.close()
                result = runner.invoke(app, [])
        assert result.exit_code == 0, result.output
        assert mock_run.called  # the dashboard coroutine is what gets run
        assert mock_asyncio_run.call_count == 1

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


# ---------------------------------------------------------------------------
# v0.2 Piece 5 — `ollama-sentinel incidents`
# ---------------------------------------------------------------------------


def _seed_incident(db_path, *, finding_id, signal="test_failure",
                   artifact="test_auth.py::test_login", symptom_file="src/app.py",
                   symptom_line=11):
    """Insert one Incident on an existing finding; return its row id."""
    db = ViolationDB(str(db_path))
    try:
        return db.persist_incident(
            Incident(
                finding_id=finding_id,
                confirming_signal=signal,
                confirming_artifact=artifact,
                symptom_file=symptom_file,
                symptom_line=symptom_line,
            )
        )
    finally:
        db.close()


class TestIncidentsCommand:
    """Tests for 'ollama-sentinel incidents' (corroborated events view)."""

    def test_incidents_shows_recent(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("COLUMNS", "200")  # widen so Rich doesn't truncate columns
        cfg = _make_report_config(tmp_path)
        db_path = tmp_path / ".ollama_reviews" / "memory.db"
        fid = _seed_one_finding_id(db_path)
        _seed_incident(db_path, finding_id=fid, artifact="test_auth.py::test_login")

        result = runner.invoke(app, ["incidents", "--config", str(cfg)])
        assert result.exit_code == 0, result.output
        assert "test_failure" in result.output
        assert "test_login" in result.output
        assert "src/app.py" in result.output

    def test_incidents_json_format(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        cfg = _make_report_config(tmp_path)
        db_path = tmp_path / ".ollama_reviews" / "memory.db"
        fid = _seed_one_finding_id(db_path)
        _seed_incident(db_path, finding_id=fid)

        result = runner.invoke(
            app, ["incidents", "--config", str(cfg), "-f", "json"]
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["finding_id"] == fid
        assert data[0]["confirming_signal"] == "test_failure"

    def test_incidents_filter_by_finding(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        cfg = _make_report_config(tmp_path)
        db_path = tmp_path / ".ollama_reviews" / "memory.db"
        # Two distinct findings, each with its own incident.
        db_path.parent.mkdir(parents=True, exist_ok=True)
        db = ViolationDB(str(db_path))
        try:
            db.persist_findings("a.py", [Finding("a.py", 1, 2, "bug", "high", "A")])
            db.persist_findings("b.py", [Finding("b.py", 5, 6, "bug", "high", "B")])
            rows = db.get_all_unresolved()
            fid_a = next(r["id"] for r in rows if r["file_path"] == "a.py")
            fid_b = next(r["id"] for r in rows if r["file_path"] == "b.py")
        finally:
            db.close()
        _seed_incident(db_path, finding_id=fid_a, artifact="test_a.py::ta")
        _seed_incident(db_path, finding_id=fid_b, artifact="test_b.py::tb")

        result = runner.invoke(
            app, ["incidents", "--config", str(cfg), "--finding", str(fid_a), "-f", "json"]
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert {d["finding_id"] for d in data} == {fid_a}

    def test_incidents_empty(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        cfg = _make_report_config(tmp_path)
        db_path = tmp_path / ".ollama_reviews" / "memory.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        ViolationDB(str(db_path)).close()  # empty DB, no incidents

        result = runner.invoke(app, ["incidents", "--config", str(cfg)])
        assert result.exit_code == 0
        assert "No incidents" in result.output

    def test_incidents_no_db(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        cfg = _make_report_config(tmp_path)
        # No DB file created.
        result = runner.invoke(app, ["incidents", "--config", str(cfg)])
        assert result.exit_code == 0
        assert "No violation database" in result.output


class TestSurfaceCommand:
    """Tests for 'ollama-sentinel surface'."""

    def test_surface_writes_sarif(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        cfg = _make_report_config(tmp_path)
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "app.py").write_text(
            "def f():\n    x = eval(data)\n"
        )
        db_path = tmp_path / ".ollama_reviews" / "memory.db"
        _seed_db(db_path, [])  # creates the DB
        db = ViolationDB(str(db_path))
        db.persist_findings("src/app.py", [
            Finding("src/app.py", 2, 2, "security", "high", "eval",
                    verbatim_excerpt="x = eval(data)"),
        ])
        db.close()

        result = runner.invoke(app, ["surface", "--config", str(cfg)])
        assert result.exit_code == 0
        sarif = tmp_path / ".ollama_reviews" / "findings.sarif"
        assert sarif.exists()
        doc = json.loads(sarif.read_text())
        assert doc["version"] == "2.1.0"
        assert "Wrote 1 findings" in result.output

    def test_surface_no_db(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        cfg = _make_report_config(tmp_path)
        result = runner.invoke(app, ["surface", "--config", str(cfg)])
        assert result.exit_code == 0
        assert "No violation database" in result.output


class TestFindingsCommand:
    """Tests for 'ollama-sentinel findings'."""

    def test_lists_open_findings_with_ids(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        cfg = _make_report_config(tmp_path)
        db_path = tmp_path / ".ollama_reviews" / "memory.db"
        _seed_db(db_path, [
            Finding("src/app.py", 10, 12, "bug", "high", "Null deref risk"),
        ])
        result = runner.invoke(app, ["findings", "--config", str(cfg)])
        assert result.exit_code == 0, result.output
        assert "Null" in result.output and "deref" in result.output
        assert "high" in result.output

    def test_json_format(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        cfg = _make_report_config(tmp_path)
        db_path = tmp_path / ".ollama_reviews" / "memory.db"
        _seed_db(db_path, [Finding("src/app.py", 1, 2, "bug", "high", "d")])
        result = runner.invoke(app, ["findings", "--config", str(cfg), "-f", "json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, list)
        assert data[0]["severity"] == "high"

    def test_severity_filter(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        cfg = _make_report_config(tmp_path)
        db_path = tmp_path / ".ollama_reviews" / "memory.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        db = ViolationDB(str(db_path))
        db.persist_findings("a.py", [Finding("a.py", 1, 1, "bug", "high", "HighOnly")])
        db.persist_findings("b.py", [Finding("b.py", 2, 2, "style", "low", "LowOnly")])
        db.close()
        result = runner.invoke(app, ["findings", "--config", str(cfg), "--severity", "low"])
        assert result.exit_code == 0
        assert "LowOnly" in result.output
        assert "HighOnly" not in result.output

    def test_file_filter(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        cfg = _make_report_config(tmp_path)
        db_path = tmp_path / ".ollama_reviews" / "memory.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        db = ViolationDB(str(db_path))
        db.persist_findings("src/app.py", [Finding("src/app.py", 1, 1, "bug", "high", "inApp")])
        db.persist_findings("other.py", [Finding("other.py", 2, 2, "style", "low", "inOther")])
        db.close()
        result = runner.invoke(app, ["findings", "--config", str(cfg), "--file", "app"])
        assert result.exit_code == 0
        assert "inApp" in result.output
        assert "inOther" not in result.output

    def test_empty_db(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        cfg = _make_report_config(tmp_path)
        db_path = tmp_path / ".ollama_reviews" / "memory.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        ViolationDB(str(db_path)).close()
        result = runner.invoke(app, ["findings", "--config", str(cfg)])
        assert result.exit_code == 0
        assert "No open findings" in result.output

    def test_no_db_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        cfg = _make_report_config(tmp_path)
        result = runner.invoke(app, ["findings", "--config", str(cfg)])
        assert result.exit_code == 0
        assert "No violation database" in result.output

    def test_limit_caps_results(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        cfg = _make_report_config(tmp_path)
        db_path = tmp_path / ".ollama_reviews" / "memory.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        db = ViolationDB(str(db_path))
        # Three distinct findings (different line numbers so upsert creates 3 rows).
        db.persist_findings("a.py", [
            Finding("a.py", 1, 1, "bug", "high", "F1"),
            Finding("a.py", 2, 2, "bug", "high", "F2"),
            Finding("a.py", 3, 3, "bug", "high", "F3"),
        ])
        db.close()
        result = runner.invoke(
            app, ["findings", "--config", str(cfg), "-f", "json", "--limit", "2"]
        )
        assert result.exit_code == 0
        assert len(json.loads(result.output)) == 2

    def test_corroborated_mark_renders(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("COLUMNS", "200")
        cfg = _make_report_config(tmp_path)
        db_path = tmp_path / ".ollama_reviews" / "memory.db"
        fid = _seed_one_finding_id(db_path)
        _seed_incident(db_path, finding_id=fid)
        result = runner.invoke(app, ["findings", "--config", str(cfg)])
        assert result.exit_code == 0, result.output
        assert "✓" in result.output


# ---------------------------------------------------------------------------
# Task 3.1 — `ollama-sentinel resolve` / `dismiss`
# ---------------------------------------------------------------------------


class TestResolveCommand:
    """Tests for 'ollama-sentinel resolve'."""

    def test_resolve_marks_fixed(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        cfg = _make_report_config(tmp_path)
        db_path = tmp_path / ".ollama_reviews" / "memory.db"
        fid = _seed_one_finding_id(db_path)
        result = runner.invoke(app, ["resolve", str(fid), "--config", str(cfg)])
        assert result.exit_code == 0, result.output
        db = ViolationDB(str(db_path))
        try:
            row = db.get_finding(fid)
            assert row["resolved"] == 1
            assert row["resolution"] == "fixed"
            assert db.get_open_findings() == []
            # CRITICAL invariant: manual close must NOT create an Incident
            assert db.get_incidents_for_finding(fid) == []
        finally:
            db.close()

    def test_resolve_missing_id(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        cfg = _make_report_config(tmp_path)
        db_path = tmp_path / ".ollama_reviews" / "memory.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        ViolationDB(str(db_path)).close()
        result = runner.invoke(app, ["resolve", "99999", "--config", str(cfg)])
        assert result.exit_code == 1
        assert "No finding with id" in result.output

    def test_resolve_no_db(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        cfg = _make_report_config(tmp_path)
        result = runner.invoke(app, ["resolve", "1", "--config", str(cfg)])
        assert result.exit_code == 1
        assert "No violation database" in result.output


class TestDismissCommand:
    """Tests for 'ollama-sentinel dismiss'."""

    def test_dismiss_marks_dismissed(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        cfg = _make_report_config(tmp_path)
        db_path = tmp_path / ".ollama_reviews" / "memory.db"
        fid = _seed_one_finding_id(db_path)
        result = runner.invoke(app, ["dismiss", str(fid), "--config", str(cfg)])
        assert result.exit_code == 0, result.output
        db = ViolationDB(str(db_path))
        try:
            row = db.get_finding(fid)
            assert row["resolved"] == 1
            assert row["resolution"] == "dismissed"
            assert db.get_open_findings() == []
            # CRITICAL invariant: manual close must NOT create an Incident
            assert db.get_incidents_for_finding(fid) == []
        finally:
            db.close()

    def test_close_already_resolved_is_idempotent(self, tmp_path, monkeypatch):
        """Closing an already-closed finding reports the existing closure and
        leaves the recorded resolution unchanged (resolve then dismiss must not
        flip 'fixed' to 'dismissed')."""
        monkeypatch.chdir(tmp_path)
        cfg = _make_report_config(tmp_path)
        db_path = tmp_path / ".ollama_reviews" / "memory.db"
        fid = _seed_one_finding_id(db_path)
        r1 = runner.invoke(app, ["resolve", str(fid), "--config", str(cfg)])
        assert r1.exit_code == 0, r1.output
        r2 = runner.invoke(app, ["dismiss", str(fid), "--config", str(cfg)])
        assert r2.exit_code == 0, r2.output
        assert "already" in r2.output.lower()
        db = ViolationDB(str(db_path))
        try:
            row = db.get_finding(fid)
            assert row["resolved"] == 1
            assert row["resolution"] == "fixed"  # NOT flipped to dismissed
        finally:
            db.close()

    def test_dismiss_missing_id(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        cfg = _make_report_config(tmp_path)
        db_path = tmp_path / ".ollama_reviews" / "memory.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        ViolationDB(str(db_path)).close()
        result = runner.invoke(app, ["dismiss", "99999", "--config", str(cfg)])
        assert result.exit_code == 1
        assert "No finding with id" in result.output
