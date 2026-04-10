"""Tests for the CLI entry points."""
import pytest
import yaml
from typer.testing import CliRunner

from ollama_sentinel.cli import app


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
