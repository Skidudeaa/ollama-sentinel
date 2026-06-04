"""
Tests for ollama_sentinel.watcher — focusing on _should_ignore and config loading.
"""
import pathlib
import textwrap

import pytest
import yaml

from ollama_sentinel.watcher import FileSentinel, _BUILTIN_IGNORE_PATTERNS, _is_likely_binary


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

class TestGroundingOverride:
    """Tests for the FileSentinel grounding_override kwarg used by --no-grounding."""

    def test_default_grounding_is_true(self, tmp_path):
        config_path = _write_config(tmp_path)
        sentinel = FileSentinel(config_path)
        assert sentinel.config.processing.grounding is True

    def test_override_to_false_flips_config(self, tmp_path):
        config_path = _write_config(tmp_path)
        sentinel = FileSentinel(config_path, grounding_override=False)
        assert sentinel.config.processing.grounding is False

    def test_override_none_preserves_yaml_value(self, tmp_path):
        """An explicit None override is a no-op — YAML wins."""
        config_path = _write_config(tmp_path)
        sentinel = FileSentinel(config_path, grounding_override=None)
        # YAML doesn't set grounding, so it defaults to True from the model.
        assert sentinel.config.processing.grounding is True


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


# ---------------------------------------------------------------------------
# Built-in ignore patterns (always-on regardless of user config)
# ---------------------------------------------------------------------------

MINIMAL_YAML_NO_PATTERNS = textwrap.dedent("""\
    watch:
      directory: "{watch_dir}"
      ignore_patterns: []
    ollama:
      host: "http://localhost:11434"
      models:
        default:
          name: "gemma3:4b"
          system_prompt: "Review this code."
    output:
      directory: ".ollama_reviews"
""")


def _sentinel_no_patterns(tmp_path: pathlib.Path) -> FileSentinel:
    config_path = tmp_path / "ollama-sentinel.yaml"
    config_path.write_text(MINIMAL_YAML_NO_PATTERNS.format(watch_dir=str(tmp_path)))
    return FileSentinel(config_path)


class TestBuiltinIgnores:
    """Built-in patterns catch noise files even when user ignore_patterns is empty."""

    def test_claude_dotdir_ignored(self, tmp_path):
        s = _sentinel_no_patterns(tmp_path)
        assert s._should_ignore(tmp_path / ".claude" / "x.py") is True

    def test_planning_dotdir_ignored(self, tmp_path):
        s = _sentinel_no_patterns(tmp_path)
        assert s._should_ignore(tmp_path / ".planning" / "intel" / ".watcher_heartbeat") is True

    def test_build_dotdir_ignored(self, tmp_path):
        s = _sentinel_no_patterns(tmp_path)
        assert s._should_ignore(
            tmp_path / ".build" / "index-build" / "arm64-apple-macosx" / "debug" / "lock.mdb"
        ) is True

    def test_node_modules_ignored(self, tmp_path):
        s = _sentinel_no_patterns(tmp_path)
        assert s._should_ignore(tmp_path / "node_modules" / "lodash" / "index.js") is True

    def test_mdb_extension_ignored(self, tmp_path):
        s = _sentinel_no_patterns(tmp_path)
        assert s._should_ignore(tmp_path / "data" / "store.mdb") is True

    def test_lock_extension_ignored(self, tmp_path):
        s = _sentinel_no_patterns(tmp_path)
        assert s._should_ignore(tmp_path / "poetry.lock") is True

    def test_watcher_heartbeat_ignored(self, tmp_path):
        s = _sentinel_no_patterns(tmp_path)
        assert s._should_ignore(tmp_path / ".watcher_heartbeat") is True

    def test_python_file_not_ignored(self, tmp_path):
        """A regular .py file must still pass through with empty user patterns."""
        s = _sentinel_no_patterns(tmp_path)
        assert s._should_ignore(tmp_path / "main.py") is False

    def test_builtin_patterns_list_not_empty(self):
        assert len(_BUILTIN_IGNORE_PATTERNS) > 0


# ---------------------------------------------------------------------------
# Size limit
# ---------------------------------------------------------------------------

class TestSizeLimit:
    """Files exceeding max_file_size_kb are ignored."""

    def _make_sentinel(self, tmp_path: pathlib.Path, max_kb: int) -> FileSentinel:
        config_path = tmp_path / "ollama-sentinel.yaml"
        cfg = yaml.safe_load(MINIMAL_YAML_NO_PATTERNS.format(watch_dir=str(tmp_path)))
        cfg["watch"]["max_file_size_kb"] = max_kb
        config_path.write_text(yaml.dump(cfg))
        return FileSentinel(config_path)

    def test_oversized_file_ignored(self, tmp_path):
        s = self._make_sentinel(tmp_path, max_kb=1)
        big = tmp_path / "big.py"
        big.write_bytes(b"x" * 2048)  # 2 KB > 1 KB limit
        assert s._should_ignore(big) is True

    def test_undersized_file_not_ignored(self, tmp_path):
        s = self._make_sentinel(tmp_path, max_kb=10)
        small = tmp_path / "small.py"
        small.write_bytes(b"x" * 100)
        assert s._should_ignore(small) is False

    def test_nonexistent_file_skips_size_check(self, tmp_path):
        """_should_ignore should not raise on a non-existent path (size check skips)."""
        s = self._make_sentinel(tmp_path, max_kb=1)
        missing = tmp_path / "ghost.py"
        # Should not raise; falls through to pattern matching
        result = s._should_ignore(missing)
        assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# Binary content sniff
# ---------------------------------------------------------------------------

class TestBinarySniff:
    """_is_likely_binary detects null bytes in first 8 KB."""

    def test_text_file_not_binary(self, tmp_path):
        f = tmp_path / "source.py"
        f.write_text("def hello(): pass\n")
        assert _is_likely_binary(f) is False

    def test_file_with_null_byte_is_binary(self, tmp_path):
        f = tmp_path / "data.bin"
        f.write_bytes(b"some data\x00more data")
        assert _is_likely_binary(f) is True

    def test_null_byte_beyond_peek_ignored(self, tmp_path):
        """Null byte beyond peek_bytes window is not detected."""
        f = tmp_path / "almost_text.dat"
        f.write_bytes(b"a" * 8192 + b"\x00")
        assert _is_likely_binary(f) is False

    def test_missing_file_returns_false(self, tmp_path):
        assert _is_likely_binary(tmp_path / "nonexistent.bin") is False


# ---------------------------------------------------------------------------
# disable_builtin_ignores opt-out
# ---------------------------------------------------------------------------

class TestDisableBuiltins:
    """Setting disable_builtin_ignores: true suppresses the built-in list."""

    def _sentinel_no_builtins(self, tmp_path: pathlib.Path) -> FileSentinel:
        config_path = tmp_path / "ollama-sentinel.yaml"
        cfg = yaml.safe_load(MINIMAL_YAML_NO_PATTERNS.format(watch_dir=str(tmp_path)))
        cfg["watch"]["disable_builtin_ignores"] = True
        cfg["watch"]["ignore_patterns"] = []
        config_path.write_text(yaml.dump(cfg))
        return FileSentinel(config_path)

    def test_claude_dotdir_not_ignored_when_disabled(self, tmp_path):
        s = self._sentinel_no_builtins(tmp_path)
        # .claude/x.py is only blocked by builtins; with them off it passes
        assert s._should_ignore(tmp_path / ".claude" / "x.py") is False

    def test_mdb_not_ignored_when_disabled(self, tmp_path):
        s = self._sentinel_no_builtins(tmp_path)
        assert s._should_ignore(tmp_path / "store.mdb") is False


# ---------------------------------------------------------------------------
# watch_filter callable
# ---------------------------------------------------------------------------

class TestWatchFilterCallable:
    """The watch_filter passed to awatch correctly gates on _should_ignore."""

    def test_filter_rejects_builtin_noise(self, tmp_path):
        """Paths that _should_ignore catches also fail the watch_filter."""
        s = _sentinel_no_patterns(tmp_path)
        from watchfiles import Change
        # Simulate the lambda watchfiles will call
        filter_fn = lambda _, p: not s._should_ignore(pathlib.Path(p))
        assert filter_fn(Change.modified, str(tmp_path / ".planning" / ".watcher_heartbeat")) is False
        assert filter_fn(Change.modified, str(tmp_path / "data" / "store.mdb")) is False

    def test_filter_passes_source_file(self, tmp_path):
        s = _sentinel_no_patterns(tmp_path)
        from watchfiles import Change
        filter_fn = lambda _, p: not s._should_ignore(pathlib.Path(p))
        assert filter_fn(Change.modified, str(tmp_path / "app.py")) is True


class TestShouldRunLegacyExtractor:
    """Pure predicate: when does the legacy regex extractor run?"""

    def test_ungrounded_always_runs(self):
        from ollama_sentinel.watcher import _should_run_legacy_extractor
        assert _should_run_legacy_extractor(False, {"summary": "x"}) is True

    def test_grounded_clean_parse_does_not_run(self):
        from ollama_sentinel.watcher import _should_run_legacy_extractor
        assert _should_run_legacy_extractor(True, {"summary": "x"}) is False

    def test_grounded_parse_failure_runs(self):
        from ollama_sentinel.watcher import _should_run_legacy_extractor
        review = {"summary": "## prose", "findings": [],
                  "grounding_parse_failed": True}
        assert _should_run_legacy_extractor(True, review) is True

    def test_wiring_calls_predicate(self):
        """Source guard: process_change must route through the predicate,
        not an inline grounding-only check (closure-testing pattern)."""
        import inspect, ollama_sentinel.watcher as w
        src = inspect.getsource(w.FileSentinel.process_change)
        assert "_should_run_legacy_extractor(" in src
        assert "elif not self.config.processing.grounding:" not in src


# ---------------------------------------------------------------------------
# SARIF auto-refresh
# ---------------------------------------------------------------------------

import json as _json
import pathlib as _pathlib

import yaml as _yaml

from watchfiles import Change
from ollama_sentinel.processor import FileChange
from ollama_sentinel.watcher import FileSentinel


def _watcher_cfg(tmp_path):
    """Config rooted at tmp_path: memory on, embeddings off (no network)."""
    cfg = {
        "watch": {"directory": str(tmp_path), "recursive": True},
        "ollama": {
            "host": "http://localhost:11434",
            "models": {"default": {"name": "m", "system_prompt": "p"}},
            "request_timeout": 30,
        },
        "processing": {"git_diff_mode": False, "grounding": True},
        "output": {"directory": ".ollama_reviews", "console_output": False},
        "memory": {"enabled": True, "db_path": ".ollama_reviews/memory.db",
                   "semantic_recall": False, "structural_recall": False},
        "embedding": {"enabled": False},
    }
    p = tmp_path / "ollama-sentinel.yaml"
    p.write_text(_yaml.dump(cfg, sort_keys=False))
    return p


def _fake_review(*_a, **_k):
    async def _run(file_change, model_role="default"):
        return {
            "summary": "review",
            "findings": [{
                "line_start": 2, "line_end": 2, "category": "security",
                "severity": "high", "verbatim_excerpt": "x = eval(data)",
                "description": "eval on untrusted input",
            }],
        }
    return _run


class TestSarifAutoRefresh:
    async def test_process_change_writes_sarif(self, tmp_path, monkeypatch):
        cfg = _watcher_cfg(tmp_path)
        src = tmp_path / "app.py"
        src.write_text("def f():\n    x = eval(data)\n    return x\n")

        sentinel = FileSentinel(cfg)
        monkeypatch.setattr(sentinel.processor, "generate_review", _fake_review())
        try:
            await sentinel.process_change(
                FileChange(path=src, change_type=Change.modified)
            )
        finally:
            await sentinel.processor.close()

        sarif = tmp_path / ".ollama_reviews" / "findings.sarif"
        assert sarif.exists()
        doc = _json.loads(sarif.read_text())
        assert doc["runs"][0]["results"][0]["ruleId"] == "ollama-sentinel/security"

    async def test_sarif_failure_does_not_break_review(self, tmp_path, monkeypatch):
        cfg = _watcher_cfg(tmp_path)
        src = tmp_path / "app.py"
        src.write_text("def f():\n    x = eval(data)\n    return x\n")

        sentinel = FileSentinel(cfg)
        monkeypatch.setattr(sentinel.processor, "generate_review", _fake_review())

        import ollama_sentinel.sarif as sarif_mod

        def _boom(*_a, **_k):
            raise RuntimeError("sarif blew up")

        monkeypatch.setattr(sarif_mod, "generate_sarif_file", _boom)
        try:
            # Must NOT raise despite the SARIF failure.
            await sentinel.process_change(
                FileChange(path=src, change_type=Change.modified)
            )
        finally:
            await sentinel.processor.close()

        # The review itself still saved.
        assert (tmp_path / ".ollama_reviews" / "app.md").exists()
