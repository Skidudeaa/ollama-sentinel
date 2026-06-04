"""Tests for ollama_sentinel.sarif — relocation and SARIF construction."""
import json

from ollama_sentinel.sarif import Relocation, build_sarif, relocate_finding


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
