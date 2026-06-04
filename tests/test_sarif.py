"""Tests for ollama_sentinel.sarif — relocation and SARIF construction."""
from ollama_sentinel.sarif import Relocation, relocate_finding


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
