"""
Tests for ollama_sentinel.watcher — focusing on _should_ignore and config loading.
"""
import pathlib
import textwrap

import pytest
import yaml

from ollama_sentinel.watcher import FileSentinel


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

MINIMAL_YAML = textwrap.dedent("""\
    watch:
      directory: "{watch_dir}"
      ignore_patterns:
        - "*.log"
        - "**/__pycache__/**"
    ollama:
      host: "http://localhost:11434"
      models:
        default:
          name: "gemma3:4b"
          system_prompt: "Review this code."
    output:
      directory: ".ollama_reviews"
""")


def _write_config(tmp_path: pathlib.Path) -> pathlib.Path:
    """Write a minimal valid YAML config and return its path."""
    config_path = tmp_path / "ollama-sentinel.yaml"
    config_path.write_text(MINIMAL_YAML.format(watch_dir=str(tmp_path)))
    return config_path


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def sentinel(tmp_path):
    """Create a FileSentinel backed by a temporary directory."""
    config_path = _write_config(tmp_path)
    return FileSentinel(config_path)


# ---------------------------------------------------------------------------
# Config loading tests
# ---------------------------------------------------------------------------

class TestConfigLoading:
    """Tests for config loading via FileSentinel.__init__."""

    def test_loads_valid_config(self, sentinel, tmp_path):
        assert sentinel.config is not None
        assert sentinel.config.watch.directory == str(tmp_path)
        assert sentinel.config.ollama.models["default"].name == "gemma3:4b"

    def test_output_directory_defaults(self, sentinel):
        assert sentinel.config.output.directory == ".ollama_reviews"

    def test_ignore_patterns_loaded(self, sentinel):
        patterns = sentinel.config.watch.ignore_patterns
        assert "*.log" in patterns
        assert "**/__pycache__/**" in patterns

    def test_raises_on_missing_config(self, tmp_path):
        with pytest.raises(ValueError, match="Failed to load configuration"):
            FileSentinel(tmp_path / "nonexistent.yaml")

    def test_raises_on_invalid_yaml(self, tmp_path):
        bad = tmp_path / "bad.yaml"
        bad.write_text(": :\n  - [invalid")
        with pytest.raises(ValueError, match="Failed to load configuration"):
            FileSentinel(bad)

    def test_raises_without_default_model(self, tmp_path):
        """Config with no 'default' model key should fail validation."""
        cfg = yaml.safe_load(MINIMAL_YAML.format(watch_dir=str(tmp_path)))
        cfg["ollama"]["models"] = {
            "security": {"name": "x", "system_prompt": "y"}
        }
        config_path = tmp_path / "no-default.yaml"
        config_path.write_text(yaml.dump(cfg))
        with pytest.raises(ValueError, match="Failed to load configuration"):
            FileSentinel(config_path)


# ---------------------------------------------------------------------------
# _should_ignore tests
# ---------------------------------------------------------------------------

class TestShouldIgnore:
    """Tests for FileSentinel._should_ignore."""

    def test_ignores_file_in_output_directory(self, sentinel, tmp_path):
        path = tmp_path / ".ollama_reviews" / "some_file.md"
        assert sentinel._should_ignore(path) is True

    def test_ignores_file_matching_pattern(self, sentinel, tmp_path):
        path = tmp_path / "debug.log"
        assert sentinel._should_ignore(path) is True

    def test_ignores_pycache_pattern(self, sentinel, tmp_path):
        path = tmp_path / "pkg" / "__pycache__" / "mod.cpython-312.pyc"
        assert sentinel._should_ignore(path) is True

    def test_does_not_ignore_normal_python_file(self, sentinel, tmp_path):
        path = tmp_path / "app.py"
        assert sentinel._should_ignore(path) is False

    def test_does_not_ignore_nested_python_file(self, sentinel, tmp_path):
        path = tmp_path / "src" / "utils.py"
        assert sentinel._should_ignore(path) is False

    def test_ignores_path_outside_watch_dir(self, sentinel):
        outside = pathlib.Path("/some/completely/other/dir/file.py")
        assert sentinel._should_ignore(outside) is True

    def test_ignores_gitignore_patterns_with_git_repo(self, tmp_path):
        """When a git repo exists with a .gitignore, those patterns are merged."""
        import subprocess

        # Initialise a real git repo so gitpython detects it
        subprocess.run(["git", "init", str(tmp_path)], check=True,
                       capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=str(tmp_path), check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"],
            cwd=str(tmp_path), check=True, capture_output=True,
        )

        # Write a .gitignore with a pattern that is NOT in the YAML config
        (tmp_path / ".gitignore").write_text("*.secret\n")

        # Enable git_diff_mode so FileProcessor picks up the repo
        cfg = yaml.safe_load(MINIMAL_YAML.format(watch_dir=str(tmp_path)))
        cfg.setdefault("processing", {})["git_diff_mode"] = True
        config_path = tmp_path / "ollama-sentinel.yaml"
        config_path.write_text(yaml.dump(cfg))

        # Need at least one commit for gitpython
        dummy = tmp_path / "init.txt"
        dummy.write_text("init")
        subprocess.run(["git", "add", "."], cwd=str(tmp_path),
                       check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=str(tmp_path),
                       check=True, capture_output=True)

        sentinel = FileSentinel(config_path)

        # .secret pattern comes from .gitignore, not YAML
        assert sentinel._should_ignore(tmp_path / "creds.secret") is True
        # Normal file still passes
        assert sentinel._should_ignore(tmp_path / "main.py") is False

    def test_deeply_nested_output_dir_ignored(self, sentinel, tmp_path):
        path = tmp_path / "a" / "b" / ".ollama_reviews" / "c" / "review.md"
        assert sentinel._should_ignore(path) is True

    def test_ignores_multiple_matching_patterns(self, sentinel, tmp_path):
        """A path matching more than one ignore pattern is still ignored."""
        # __pycache__/*.log matches both patterns
        path = tmp_path / "__pycache__" / "trace.log"
        assert sentinel._should_ignore(path) is True
