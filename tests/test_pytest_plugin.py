"""Tests for the ollama_sentinel pytest plugin (Piece 4).

The plugin links objective test failures to open Findings, creating
Incidents. These tests exercise the pure matching/extraction helpers
directly (fast, deterministic) plus the wired hook behavior via the
``pytester`` fixture.
"""

import textwrap

import pytest
import yaml

from ollama_sentinel import pytest_plugin as plug
from ollama_sentinel.violation_db import Finding, ViolationDB


class TestMatchFindings:
    """A6 — tolerance-window overlap between a failure line and a Finding."""

    def _finding(self, line_start, line_end, fid=1, path="auth/session.py"):
        return {
            "id": fid,
            "file_path": path,
            "line_start": line_start,
            "line_end": line_end,
        }

    def test_failure_inside_finding_range_matches(self):
        findings = [self._finding(30, 45)]
        assert plug._match_findings(findings, failure_line=33, tolerance=5) == findings

    def test_failure_within_tolerance_above_range_matches(self):
        # finding 30-30, failure at 33, tolerance 5 -> 33 <= 30+5 -> match
        findings = [self._finding(30, 30)]
        assert plug._match_findings(findings, failure_line=33, tolerance=5) == findings

    def test_failure_beyond_tolerance_does_not_match(self):
        # finding 30-30, failure at 40, tolerance 5 -> 40 > 35 -> no match
        findings = [self._finding(30, 30)]
        assert plug._match_findings(findings, failure_line=40, tolerance=5) == []

    def test_failure_within_tolerance_below_range_matches(self):
        # finding 30-45, failure at 26, tolerance 5 -> 26 >= 30-5 -> match
        findings = [self._finding(30, 45)]
        assert plug._match_findings(findings, failure_line=26, tolerance=5) == findings


class _FakeReprCrash:
    def __init__(self, path, lineno):
        self.path = path
        self.lineno = lineno


class _FakeLongRepr:
    def __init__(self, reprcrash):
        self.reprcrash = reprcrash


class _FakeReport:
    def __init__(self, longrepr):
        self.longrepr = longrepr


class TestExtractFailureLocation:
    """Pull the crash (path, line) from a failed report's traceback."""

    def test_extracts_path_and_line_from_reprcrash(self):
        report = _FakeReport(_FakeLongRepr(_FakeReprCrash("/repo/auth/session.py", 42)))
        assert plug._extract_failure_location(report) == ("/repo/auth/session.py", 42)

    def test_returns_none_when_no_crash_info(self):
        report = _FakeReport(longrepr=None)
        assert plug._extract_failure_location(report) is None

    def test_returns_none_when_longrepr_is_a_string(self):
        # collection / internal errors give a plain-string longrepr
        report = _FakeReport(longrepr="some collection error text")
        assert plug._extract_failure_location(report) is None


class TestRankSuspectCommits:
    """suspect_commits = recent commits touching the failing file or imports."""

    # newest-first history: (sha, files_touched)
    HISTORY = [
        ("c1", ["docs/readme.md"]),
        ("c2", ["auth/session.py"]),
        ("c3", ["auth/helpers.py"]),
        ("c4", ["auth/session.py", "auth/helpers.py"]),
        ("c5", ["unrelated.py"]),
    ]

    def test_keeps_only_commits_touching_file_or_neighbors_in_recency_order(self):
        result = plug._rank_suspect_commits(
            failing_file="auth/session.py",
            neighbor_files=["auth/helpers.py"],
            recent_commits=self.HISTORY,
            limit=5,
        )
        assert result == ["c2", "c3", "c4"]

    def test_excludes_commits_touching_unrelated_files(self):
        result = plug._rank_suspect_commits(
            failing_file="auth/session.py",
            neighbor_files=[],
            recent_commits=self.HISTORY,
            limit=5,
        )
        assert result == ["c2", "c4"]

    def test_truncates_to_limit(self):
        result = plug._rank_suspect_commits(
            failing_file="auth/session.py",
            neighbor_files=["auth/helpers.py"],
            recent_commits=self.HISTORY,
            limit=2,
        )
        assert result == ["c2", "c3"]

    def test_returns_empty_when_no_history(self):
        assert plug._rank_suspect_commits(
            failing_file="auth/session.py",
            neighbor_files=[],
            recent_commits=[],
            limit=5,
        ) == []


# --------------------------------------------------------------------------- #
# Wired-hook integration tests (via the in-process ``pytester`` fixture).
#
# Each scaffolds a throwaway project: a source module that raises at a known
# line, a test that triggers it, a seeded ViolationDB, and the opt-in config.
# We run pytest *inside* that project and assert the plugin turned the real
# failure into an Incident linked to the right Finding.
# --------------------------------------------------------------------------- #


def _seed_finding(db_path, finding):
    """Create the DB, persist one Finding, return its row id."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = ViolationDB(str(db_path))
    try:
        db.persist_findings(finding.file_path, [finding])
        rows = db.get_all_unresolved()
        return rows[0]["id"]
    finally:
        db.close()


def _scaffold(pytester, *, source, test_body, write_yaml=True):
    """Lay down source + test + opt-in ini (and optionally the yaml).

    Returns the db_path (``.ollama_reviews/memory.db`` under the project).
    The caller seeds Findings before/after as needed.
    """
    proj = pytester.path
    (proj / "mymod.py").write_text(textwrap.dedent(source))
    (proj / "test_mymod.py").write_text(textwrap.dedent(test_body))
    pytester.makefile(
        ".ini",
        pytest="[pytest]\nollama_sentinel = true\n",
    )
    db_path = proj / ".ollama_reviews" / "memory.db"
    if write_yaml:
        cfg = {
            "watch": {"directory": str(proj)},
            "ollama": {
                "host": "http://localhost:11434",
                "models": {"default": {"name": "m", "system_prompt": "p"}},
            },
            "memory": {"enabled": True, "db_path": ".ollama_reviews/memory.db"},
        }
        (proj / "ollama-sentinel.yaml").write_text(yaml.dump(cfg, sort_keys=False))
    return db_path


def _incidents(db_path):
    db = ViolationDB(str(db_path))
    try:
        return db.get_recent_incidents(days=3650, limit=100)
    finally:
        db.close()


# A source module whose ``raise`` lands on a known line. Line 1 is the blank
# produced by dedent's leading newline; ``def`` is line 2, ``raise`` is line 3.
_RAISING_SOURCE = """
def boom():
    raise ValueError("kaboom")
"""
_RAISE_LINE = 3


@pytest.fixture
def _explicit_plugin_load(monkeypatch):
    """Load the plugin into the inner pytester run via ``-p`` only.

    Disabling entry-point autoload makes these tests robust to editable-install
    state and avoids a double-registration error when the plugin *is* installed
    (autoload + ``-p`` would register the same module object twice).
    """
    monkeypatch.setenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")


@pytest.mark.usefixtures("_explicit_plugin_load")
class TestPluginIntegration:
    """End-to-end: a real test failure becomes an Incident."""

    def test_plugin_creates_incident_on_matching_failure(self, pytester):
        db_path = _scaffold(
            pytester,
            source=_RAISING_SOURCE,
            test_body="""
            from mymod import boom

            def test_it():
                boom()
            """,
        )
        fid = _seed_finding(
            db_path,
            Finding("mymod.py", _RAISE_LINE, _RAISE_LINE, "bug", "high", "raises"),
        )

        result = pytester.runpytest("-p", "ollama_sentinel.pytest_plugin")
        result.assert_outcomes(failed=1)

        incidents = _incidents(db_path)
        assert len(incidents) == 1
        inc = incidents[0]
        assert inc["finding_id"] == fid
        assert inc["confirming_signal"] == "test_failure"
        assert "test_it" in inc["confirming_artifact"]
        assert inc["symptom_file"] == "mymod.py"
        assert inc["symptom_line"] == _RAISE_LINE

    def test_plugin_skips_when_no_matching_finding(self, pytester):
        # A4: the failure is at mymod.py:3, but the only Finding is far away.
        db_path = _scaffold(
            pytester,
            source=_RAISING_SOURCE,
            test_body="""
            from mymod import boom

            def test_it():
                boom()
            """,
        )
        _seed_finding(
            db_path,
            Finding("mymod.py", 90, 95, "bug", "high", "elsewhere"),
        )

        result = pytester.runpytest("-p", "ollama_sentinel.pytest_plugin")
        result.assert_outcomes(failed=1)
        assert _incidents(db_path) == []

    def test_plugin_tolerance_window(self, pytester):
        # A6: Finding at 30-30, failure at line 33 (default tolerance 5) -> match.
        padded = "\n" * 31 + 'def boom():\n    raise ValueError("x")\n'
        # blank lines 1-31, ``def`` line 32, ``raise`` line 33
        db_path = _scaffold(
            pytester,
            source=padded,
            test_body="""
            from mymod import boom

            def test_it():
                boom()
            """,
        )
        _seed_finding(
            db_path,
            Finding("mymod.py", 30, 30, "bug", "high", "near"),
        )

        result = pytester.runpytest("-p", "ollama_sentinel.pytest_plugin")
        result.assert_outcomes(failed=1)
        incidents = _incidents(db_path)
        assert len(incidents) == 1
        assert incidents[0]["symptom_line"] == 33

    def test_plugin_noop_without_config(self, pytester):
        # No ollama-sentinel.yaml: the plugin must do nothing (and not error),
        # even though a DB with a matching Finding exists.
        db_path = _scaffold(
            pytester,
            source=_RAISING_SOURCE,
            test_body="""
            from mymod import boom

            def test_it():
                boom()
            """,
            write_yaml=False,
        )
        _seed_finding(
            db_path,
            Finding("mymod.py", _RAISE_LINE, _RAISE_LINE, "bug", "high", "raises"),
        )

        result = pytester.runpytest("-p", "ollama_sentinel.pytest_plugin")
        result.assert_outcomes(failed=1)
        assert _incidents(db_path) == []

    def test_plugin_multiple_failures_same_finding(self, pytester):
        # A1: two failing tests both crash at mymod.py:3 -> two Incidents on
        # the one Finding, with distinct confirming_artifact node ids.
        db_path = _scaffold(
            pytester,
            source=_RAISING_SOURCE,
            test_body="""
            from mymod import boom

            def test_one():
                boom()

            def test_two():
                boom()
            """,
        )
        fid = _seed_finding(
            db_path,
            Finding("mymod.py", _RAISE_LINE, _RAISE_LINE, "bug", "high", "raises"),
        )

        result = pytester.runpytest("-p", "ollama_sentinel.pytest_plugin")
        result.assert_outcomes(failed=2)
        incidents = _incidents(db_path)
        assert len(incidents) == 2
        assert {i["finding_id"] for i in incidents} == {fid}
        artifacts = {i["confirming_artifact"] for i in incidents}
        assert len(artifacts) == 2
        assert any("test_one" in a for a in artifacts)
        assert any("test_two" in a for a in artifacts)


@pytest.mark.usefixtures("_explicit_plugin_load")
class TestPluginGitContext:
    """When the project is a git repo, populate triggering/suspect commits."""

    def test_incident_records_head_and_suspect_commit(self, pytester):
        import git

        db_path = _scaffold(
            pytester,
            source=_RAISING_SOURCE,
            test_body="""
            from mymod import boom

            def test_it():
                boom()
            """,
        )
        fid = _seed_finding(
            db_path,
            Finding("mymod.py", _RAISE_LINE, _RAISE_LINE, "bug", "high", "raises"),
        )
        repo = git.Repo.init(pytester.path)
        repo.index.add(["mymod.py"])
        commit = repo.index.commit("add boom")

        result = pytester.runpytest("-p", "ollama_sentinel.pytest_plugin")
        result.assert_outcomes(failed=1)

        incidents = _incidents(db_path)
        assert len(incidents) == 1
        inc = incidents[0]
        assert inc["finding_id"] == fid
        assert inc["triggering_commit"] == commit.hexsha
        assert commit.hexsha in (inc["suspect_commits"] or [])
