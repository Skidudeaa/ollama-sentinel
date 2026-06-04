"""Tests for ollama_sentinel.remediate — localized fix generation."""

from ollama_sentinel.remediate import (
    ProposedFix,
    build_fix_prompt,
    parse_fix_response,
    propose_fix,
    splice_lines,
)
from ollama_sentinel.sarif import Relocation


# ---------------------------------------------------------------------------
# splice_lines
# ---------------------------------------------------------------------------


class TestSpliceLines:
    def test_single_line_replace(self):
        content = "a\nb\nc\n"
        assert splice_lines(content, 2, 2, "B") == "a\nB\nc\n"

    def test_multi_line_replace(self):
        content = "a\nb\nc\nd\n"
        assert splice_lines(content, 2, 3, "B\nC") == "a\nB\nC\nd\n"

    def test_replace_first_line(self):
        content = "a\nb\nc\n"
        assert splice_lines(content, 1, 1, "A") == "A\nb\nc\n"

    def test_replace_last_line_with_trailing_newline(self):
        content = "a\nb\nc\n"
        assert splice_lines(content, 3, 3, "C") == "a\nb\nC\n"

    def test_replace_last_line_without_trailing_newline(self):
        content = "a\nb\nc"
        assert splice_lines(content, 3, 3, "C") == "a\nb\nC"

    def test_indentation_preserved_in_replacement(self):
        content = "def f():\n    x = 1\n    return x\n"
        out = splice_lines(content, 2, 2, "    x = 2")
        assert out == "def f():\n    x = 2\n    return x\n"

    def test_surrounding_lines_untouched(self):
        content = "h1\nh2\nTARGET\nf1\nf2\n"
        out = splice_lines(content, 3, 3, "FIXED")
        assert out.splitlines() == ["h1", "h2", "FIXED", "f1", "f2"]

    def test_replacement_with_trailing_newline_not_doubled(self):
        content = "a\nb\nc\n"
        assert splice_lines(content, 2, 2, "B\n") == "a\nB\nc\n"

    def test_preserves_crlf_terminator_on_replaced_line(self):
        # The replaced line must keep the file's CRLF terminator, not get a bare
        # LF that leaves the file with mixed endings.
        content = "a\r\nb\r\nc\r\n"
        assert splice_lines(content, 2, 2, "B") == "a\r\nB\r\nc\r\n"

    def test_normalizes_replacement_newlines_to_crlf_file(self):
        content = "a\r\nb\r\nc\r\nd\r\n"
        assert splice_lines(content, 2, 3, "B\nC") == "a\r\nB\r\nC\r\nd\r\n"

    def test_out_of_range_span_raises(self):
        # A span starting beyond EOF must fail loudly, not silently append the
        # replacement to the end of the file.
        import pytest
        with pytest.raises(ValueError):
            splice_lines("a\nb\n", 5, 5, "X")
        with pytest.raises(ValueError):
            splice_lines("a\nb\n", 2, 1, "X")  # end < start


# ---------------------------------------------------------------------------
# parse_fix_response
# ---------------------------------------------------------------------------


class TestParseFixResponse:
    def test_fenced_block_stripped(self):
        raw = "```python\nx = safe(data)\n```"
        assert parse_fix_response(raw) == "x = safe(data)"

    def test_fence_without_language_stripped(self):
        raw = "```\nx = 1\n```"
        assert parse_fix_response(raw) == "x = 1"

    def test_bare_code_passes_through(self):
        raw = "x = safe(data)"
        assert parse_fix_response(raw) == "x = safe(data)"

    def test_leading_trailing_blank_lines_trimmed(self):
        raw = "\n\n```\nx = 1\n```\n\n"
        assert parse_fix_response(raw) == "x = 1"

    def test_unmatched_opening_fence_not_stripped(self):
        # A dangling opener (no closer) must pass through unchanged — never
        # corrupt content by stripping half a fence.
        raw = "```python\nx = 1"
        assert parse_fix_response(raw) == raw

    def test_embedded_fence_without_wrapping_pair_not_stripped(self):
        # Markdown/docstring content that contains a fence but is not itself a
        # wrapped block must pass through untouched.
        raw = "Here is code:\n```\nx = 1\n```\nmore text"
        assert parse_fix_response(raw) == raw

    def test_multiline_fenced_body_preserved(self):
        raw = "```\na = 1\nb = 2\n```"
        assert parse_fix_response(raw) == "a = 1\nb = 2"


# ---------------------------------------------------------------------------
# build_fix_prompt
# ---------------------------------------------------------------------------


def _finding(**over):
    base = dict(
        severity="high", category="security",
        description="eval on untrusted input",
        verbatim_excerpt="x = eval(data)",
    )
    base.update(over)
    return base


class TestBuildFixPrompt:
    def test_includes_target_range_and_finding_metadata(self):
        content = "\n".join(f"line{i}" for i in range(1, 31)) + "\n"
        prompt = build_fix_prompt(content, 10, 11, _finding())
        assert "10" in prompt and "11" in prompt
        assert "high" in prompt
        assert "security" in prompt
        assert "eval on untrusted input" in prompt
        assert "x = eval(data)" in prompt

    def test_window_clamps_at_file_start(self):
        content = "a\nb\nc\n"
        # target line 1 with default ctx would request lines below 1
        prompt = build_fix_prompt(content, 1, 1, _finding(), ctx=15)
        # no zero or negative line numbers in the rendered window
        for tok in prompt.split():
            if tok.lstrip("-").isdigit():
                assert int(tok) >= 1

    def test_window_clamps_at_file_end(self):
        content = "a\nb\nc\n"
        prompt = build_fix_prompt(content, 3, 3, _finding(), ctx=15)
        # the file has 3 lines; the window cannot render a line-4 entry
        # (rendered as "<num> | ", so check for the "4 |" line marker).
        assert "4 |" not in prompt


# ---------------------------------------------------------------------------
# propose_fix
# ---------------------------------------------------------------------------


class _FakeClient:
    def __init__(self, response):
        self.response = response
        self.calls = []

    async def generate_review(self, role, prompt, **kwargs):
        self.calls.append({"role": role, "prompt": prompt, "kwargs": kwargs})
        return self.response


class TestProposeFix:
    async def test_ok_splices_model_replacement(self):
        content = "def f():\n    x = eval(data)\n    return x\n"
        reloc = Relocation(2, 2, "relocated", exact=True)
        client = _FakeClient("    x = safe(data)")
        fix = await propose_fix(content, _finding(), reloc, client)
        assert isinstance(fix, ProposedFix)
        assert fix.status == "ok"
        assert fix.new_content == "def f():\n    x = safe(data)\n    return x\n"
        assert fix.start == 2 and fix.end == 2

    async def test_identical_replacement_is_no_change(self):
        content = "def f():\n    x = eval(data)\n    return x\n"
        reloc = Relocation(2, 2, "relocated", exact=True)
        client = _FakeClient("    x = eval(data)")
        fix = await propose_fix(content, _finding(), reloc, client)
        assert fix.status == "no_change"
        assert fix.new_content == content

    async def test_client_called_with_plain_text_no_response_format(self):
        content = "a\nx = eval(data)\nc\n"
        reloc = Relocation(2, 2, "relocated", exact=True)
        client = _FakeClient("x = safe(data)")
        await propose_fix(content, _finding(), reloc, client)
        assert len(client.calls) == 1
        assert "response_format" not in client.calls[0]["kwargs"]

    async def test_refuses_non_exact_relocation(self):
        # Defense-in-depth: propose_fix must not splice a non-exact (fuzzy) or
        # stale/stored relocation even if a caller forgets to gate on it.
        import pytest
        content = "def f():\n    x = eval(data)\n    return x\n"
        client = _FakeClient("    x = safe(data)")
        for reloc in (
            Relocation(2, 2, "relocated", exact=False),  # fuzzy
            Relocation(2, 2, "stored", exact=False),
            Relocation(2, 2, "stale", exact=False),
        ):
            with pytest.raises(ValueError):
                await propose_fix(content, _finding(), reloc, client)
        assert client.calls == []  # never reached the model
