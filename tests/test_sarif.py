"""Tests for ollama_sentinel.sarif — relocation and SARIF construction."""
import json

from ollama_sentinel.sarif import build_sarif, relocate_finding


def _finding(**over):
    base = dict(
        id=1, file_path="src/app.py", line_start=2, line_end=2,
        category="security", severity="high",
        description="eval on untrusted input",
        verbatim_excerpt="x = eval(data)", occurrence_count=1,
    )
    base.update(over)
    return base


class TestRelocateFinding:
    def test_exact_match_at_stored_line(self):
        content = "def f():\n    x = eval(data)\n    return x\n"
        reloc = relocate_finding(content, _finding(line_start=2, line_end=2))
        assert (reloc.start_line, reloc.end_line) == (2, 2)
        assert reloc.status == "relocated"

    def test_drifted_match_found_at_new_line(self):
        # Two lines inserted above; excerpt now on line 4.
        content = "import os\nimport sys\ndef f():\n    x = eval(data)\n"
        reloc = relocate_finding(content, _finding(line_start=2, line_end=2))
        assert reloc.start_line == 4
        assert reloc.status == "relocated"

    def test_multiple_matches_picks_nearest_to_stored(self):
        content = "x = eval(data)\n\n\nx = eval(data)\n"  # lines 1 and 4
        reloc = relocate_finding(content, _finding(line_start=4, line_end=4))
        assert reloc.start_line == 4

    def test_excerpt_not_found_is_stale(self):
        content = "def f():\n    return safe(data)\n"
        reloc = relocate_finding(content, _finding())
        assert reloc.status == "stale"

    def test_empty_excerpt_falls_back_to_stored_lines(self):
        content = "anything\n"
        reloc = relocate_finding(
            content, _finding(verbatim_excerpt="", line_start=7, line_end=9)
        )
        assert (reloc.start_line, reloc.end_line) == (7, 9)
        assert reloc.status == "stored"

    def test_reindented_excerpt_still_matches(self):
        # Excerpt stored with 4-space indent; file now uses 8 spaces.
        content = "def f():\n        x = eval(data)\n"
        reloc = relocate_finding(content, _finding(line_start=2, line_end=2))
        assert reloc.status == "relocated"
        assert reloc.start_line == 2

    def test_multiline_excerpt_relocates_span(self):
        content = "import os\n\ndef f():\n    a = 1\n    return secret(a)\n"
        f = _finding(
            line_start=10, line_end=11,  # stale stored lines
            verbatim_excerpt="def f():\n    a = 1\n    return secret(a)",
        )
        reloc = relocate_finding(content, f)
        assert reloc.status == "relocated"
        assert (reloc.start_line, reloc.end_line) == (3, 5)

    def test_multiline_flattened_excerpt_relocates(self):
        # The model emitted a multi-line region as a single flattened line
        # (newlines collapsed to spaces). Line-block matching misses it; the
        # word-sequence fallback finds it and maps back to the line span.
        content = "import os\n\ndef f():\n    a = 1\n    return secret(a)\n"
        f = _finding(
            line_start=10, line_end=11,  # stale stored lines
            verbatim_excerpt="def f(): a = 1 return secret(a)",
        )
        reloc = relocate_finding(content, f)
        assert reloc.status == "relocated"
        assert (reloc.start_line, reloc.end_line) == (3, 5)

    def test_exact_whole_line_match_sets_exact_true(self):
        content = "def f():\n    x = eval(data)\n    return x\n"
        reloc = relocate_finding(content, _finding(line_start=2, line_end=2))
        assert reloc.status == "relocated"
        assert reloc.exact is True

    def test_flattened_word_match_sets_exact_false(self):
        # The word-sequence fallback relocates but is NOT a whole-line block
        # match — its span can straddle line boundaries, so it is not exact.
        content = "import os\n\ndef f():\n    a = 1\n    return secret(a)\n"
        f = _finding(
            line_start=10, line_end=11,
            verbatim_excerpt="def f(): a = 1 return secret(a)",
        )
        reloc = relocate_finding(content, f)
        assert reloc.status == "relocated"
        assert reloc.exact is False

    def test_stale_and_stored_are_not_exact(self):
        stale = relocate_finding("def f():\n    return safe(data)\n", _finding())
        assert stale.status == "stale" and stale.exact is False
        stored = relocate_finding(
            "anything\n", _finding(verbatim_excerpt="", line_start=7, line_end=9)
        )
        assert stored.status == "stored" and stored.exact is False


class TestBuildSarif:
    def _located(self, **over):
        f = _finding(**over)
        return [(f, relocate_finding("    x = eval(data)\n", f))]

    def test_basic_document_shape(self):
        doc = build_sarif(self._located(), tool_version="0.1.1")
        assert doc["version"] == "2.1.0"
        run = doc["runs"][0]
        assert run["tool"]["driver"]["name"] == "ollama-sentinel"
        assert run["tool"]["driver"]["version"] == "0.1.1"
        assert len(run["results"]) == 1

    def test_severity_maps_to_level(self):
        cases = {"critical": "error", "high": "error",
                 "medium": "warning", "low": "note", "weird": "warning"}
        for severity, level in cases.items():
            doc = build_sarif(self._located(severity=severity), tool_version="x")
            assert doc["runs"][0]["results"][0]["level"] == level

    def test_rules_deduped_by_category(self):
        f1 = _finding(id=1, category="bug")
        f2 = _finding(id=2, category="bug")
        f3 = _finding(id=3, category="security")
        located = [(f, relocate_finding("x = eval(data)\n", f)) for f in (f1, f2, f3)]
        doc = build_sarif(located, tool_version="x")
        rule_ids = {r["id"] for r in doc["runs"][0]["tool"]["driver"]["rules"]}
        assert rule_ids == {"ollama-sentinel/bug", "ollama-sentinel/security"}

    def test_result_location_and_snippet(self):
        doc = build_sarif(self._located(), tool_version="x")
        loc = doc["runs"][0]["results"][0]["locations"][0]["physicalLocation"]
        assert loc["artifactLocation"]["uri"] == "src/app.py"
        assert loc["region"]["snippet"]["text"] == "x = eval(data)"

    def test_fingerprint_stable_across_line_drift(self):
        # Same finding, different stored lines → identical fingerprint.
        a = _finding(line_start=2)
        b = _finding(line_start=99)
        fa = build_sarif([(a, relocate_finding("x = eval(data)\n", a))],
                         tool_version="x")
        fb = build_sarif([(b, relocate_finding("x = eval(data)\n", b))],
                         tool_version="x")
        fp_a = fa["runs"][0]["results"][0]["partialFingerprints"]["ollamaSentinel/v1"]
        fp_b = fb["runs"][0]["results"][0]["partialFingerprints"]["ollamaSentinel/v1"]
        assert fp_a == fp_b

    def test_corroborated_flag_from_ids(self):
        doc = build_sarif(self._located(id=7), tool_version="x",
                          corroborated_ids=frozenset({7}))
        assert doc["runs"][0]["results"][0]["properties"]["corroborated"] is True

    def test_rule_level_promotes_to_worst_severity(self):
        # Same category, low then critical → rule badge must be "error".
        f_lo = _finding(id=1, category="bug", severity="low")
        f_hi = _finding(id=2, category="bug", severity="critical")
        located = [(f, relocate_finding("x = eval(data)\n", f)) for f in (f_lo, f_hi)]
        doc = build_sarif(located, tool_version="x")
        rules = doc["runs"][0]["tool"]["driver"]["rules"]
        bug_rule = next(r for r in rules if r["id"] == "ollama-sentinel/bug")
        assert bug_rule["defaultConfiguration"]["level"] == "error"

    def test_snippet_omitted_when_excerpt_empty(self):
        doc = build_sarif(self._located(verbatim_excerpt=""), tool_version="x")
        region = doc["runs"][0]["results"][0]["locations"][0]["physicalLocation"]["region"]
        assert "snippet" not in region


import pathlib

from ollama_sentinel.sarif import generate_sarif_file
from ollama_sentinel.violation_db import Finding, ViolationDB


def _seed(db_path: pathlib.Path, file_path: str, findings):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = ViolationDB(str(db_path))
    db.persist_findings(file_path, findings)
    db.close()


class TestGenerateSarifFile:
    def test_writes_sarif_for_open_findings(self, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "app.py").write_text(
            "def f():\n    x = eval(data)\n    return x\n"
        )
        db_path = tmp_path / ".ollama_reviews" / "memory.db"
        _seed(db_path, "src/app.py", [
            Finding("src/app.py", 2, 2, "security", "high",
                    "eval on untrusted input",
                    verbatim_excerpt="x = eval(data)"),
        ])

        db = ViolationDB(str(db_path))
        try:
            summary = generate_sarif_file(
                db, tmp_path, tmp_path / ".ollama_reviews", tool_version="0.1.1",
            )
        finally:
            db.close()

        assert summary.emitted == 1
        assert summary.relocated == 1
        assert summary.stale == 0
        doc = json.loads(summary.path.read_text())
        assert doc["runs"][0]["results"][0]["ruleId"] == "ollama-sentinel/security"

    def test_stale_finding_excluded_and_counted(self, tmp_path):
        (tmp_path / "src").mkdir()
        # File no longer contains the excerpt.
        (tmp_path / "src" / "app.py").write_text("def f():\n    return ok\n")
        db_path = tmp_path / ".ollama_reviews" / "memory.db"
        _seed(db_path, "src/app.py", [
            Finding("src/app.py", 2, 2, "security", "high", "gone",
                    verbatim_excerpt="x = eval(data)"),
        ])

        db = ViolationDB(str(db_path))
        try:
            summary = generate_sarif_file(
                db, tmp_path, tmp_path / ".ollama_reviews", tool_version="x",
            )
        finally:
            db.close()

        assert summary.emitted == 0
        assert summary.stale == 1
        doc = json.loads(summary.path.read_text())
        assert doc["runs"][0]["results"] == []

    def test_missing_file_counts_as_stale(self, tmp_path):
        db_path = tmp_path / ".ollama_reviews" / "memory.db"
        _seed(db_path, "src/gone.py", [
            Finding("src/gone.py", 1, 1, "bug", "low", "x",
                    verbatim_excerpt="whatever"),
        ])
        db = ViolationDB(str(db_path))
        try:
            summary = generate_sarif_file(
                db, tmp_path, tmp_path / ".ollama_reviews", tool_version="x",
            )
        finally:
            db.close()
        assert summary.stale == 1
        assert summary.emitted == 0

    def test_empty_excerpt_counts_as_unverified(self, tmp_path):
        # Finding with no verbatim_excerpt on a present file → status "stored"
        # → unverified += 1, still emitted.
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "app.py").write_text("def f():\n    pass\n")
        db_path = tmp_path / ".ollama_reviews" / "memory.db"
        _seed(db_path, "src/app.py", [
            Finding("src/app.py", 1, 1, "bug", "low", "no excerpt",
                    verbatim_excerpt=""),
        ])
        db = ViolationDB(str(db_path))
        try:
            summary = generate_sarif_file(
                db, tmp_path, tmp_path / ".ollama_reviews", tool_version="x",
            )
        finally:
            db.close()
        assert summary.emitted == 1
        assert summary.unverified == 1
        assert summary.relocated == 0
        assert summary.stale == 0

    def test_empty_db_writes_valid_sarif_with_zero_results(self, tmp_path):
        # No findings at all → SARIF document is valid but contains no results.
        db_path = tmp_path / ".ollama_reviews" / "memory.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        db = ViolationDB(str(db_path))
        try:
            summary = generate_sarif_file(
                db, tmp_path, tmp_path / ".ollama_reviews", tool_version="x",
            )
        finally:
            db.close()
        assert summary.emitted == 0
        assert summary.stale == 0
        doc = json.loads(summary.path.read_text())
        assert doc["version"] == "2.1.0"
        assert doc["runs"][0]["results"] == []

    def test_out_path_override_honored(self, tmp_path):
        # Passing out_path writes to the given location, not the default.
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "app.py").write_text(
            "def f():\n    x = eval(data)\n"
        )
        db_path = tmp_path / ".ollama_reviews" / "memory.db"
        _seed(db_path, "src/app.py", [
            Finding("src/app.py", 2, 2, "security", "high", "eval",
                    verbatim_excerpt="x = eval(data)"),
        ])
        custom_out = tmp_path / "custom" / "out.sarif"
        db = ViolationDB(str(db_path))
        try:
            summary = generate_sarif_file(
                db, tmp_path, tmp_path / ".ollama_reviews",
                tool_version="x", out_path=custom_out,
            )
        finally:
            db.close()
        assert summary.path == custom_out
        assert custom_out.exists()
        assert not (tmp_path / ".ollama_reviews" / "findings.sarif").exists()
