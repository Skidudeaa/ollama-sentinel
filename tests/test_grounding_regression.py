"""Slop-regression suite (R1-R4) — empirical seal on the reviewer-grounding fix.

The May-3 retro (docs/retros/2026-05-03-config-and-timeout-debugging.md:101-109)
identified slop categories the pre-grounding pipeline emitted regardless of
whether the issues existed in the reviewed file. These tests construct
synthetic schema-conformant model responses that simulate those slop
categories and assert the post-Step-1 validator drops them.

Cases:
    R1 — Magic-numbers slop on a file with no magic numbers.
    R2 — Stale numeric quote (verbatim drift on numeric values).
    R3 — Whitespace-drift survives validation (G4 from the grounding spec).
    R4 — Mixed batch: one slop finding + one valid finding; only the valid
         one survives (G1 from the grounding spec).
"""
import asyncio

import pytest

from ollama_sentinel.extractor import _validate_verbatim, validate_findings


# ---------------------------------------------------------------------------
# R1, R2 — pure-slop findings: validator must drop them with a WARNING log
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "case_id, file_content, finding",
    [
        # R1 — Model claims a MAX_RETRIES magic number on a file that has none.
        (
            "R1_magic_numbers",
            "def add(a, b): return a + b\n",
            {
                "line_start": 1,
                "line_end": 1,
                "category": "maintainability",
                "severity": "low",
                "verbatim_excerpt": "MAX_RETRIES = 5",
                "description": "Magic number should be a named constant.",
            },
        ),
        # R2 — Model quotes TIMEOUT = 120 when the file actually says TIMEOUT = 30.
        (
            "R2_stale_numeric",
            "TIMEOUT = 30\n",
            {
                "line_start": 1,
                "line_end": 1,
                "category": "bug",
                "severity": "medium",
                "verbatim_excerpt": "TIMEOUT = 120",
                "description": "Timeout value is too high.",
            },
        ),
    ],
    ids=["R1_magic_numbers", "R2_stale_numeric"],
)
def test_slop_finding_is_dropped_with_warning(
    case_id, file_content, finding, caplog
):
    """A finding whose verbatim_excerpt is not in the file is dropped + WARNING."""
    # Sanity: the validator helper agrees the excerpt isn't in the file.
    assert _validate_verbatim(finding, file_content) is False

    with caplog.at_level("WARNING"):
        result = asyncio.run(
            validate_findings([finding], "synthetic.py", file_content)
        )

    assert result == [], f"{case_id}: slop finding should have been dropped"

    # WARNING was emitted naming the file and the cited line range.
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert warnings, f"{case_id}: expected a WARNING log for the dropped finding"

    combined = " ".join(r.getMessage() for r in warnings)
    assert "synthetic.py" in combined, f"{case_id}: WARNING must name the file"
    assert "1-1" in combined, f"{case_id}: WARNING must name the line range"
    assert finding["verbatim_excerpt"] in combined, (
        f"{case_id}: WARNING must include the rejected excerpt"
    )


# ---------------------------------------------------------------------------
# R3 — whitespace drift (tabs vs spaces) must still pass validation (G4)
# ---------------------------------------------------------------------------


def test_whitespace_drift_excerpt_is_accepted():
    """Tabs in the file vs single spaces in the excerpt should both normalise.

    Spec G4: the validator collapses runs of whitespace to single spaces on
    BOTH sides before substring-matching, so a model echoing the line with
    different indentation should still be accepted.
    """
    file_content = "\tif foo:\n\t\tbar()\n"
    finding = {
        "line_start": 1,
        "line_end": 2,
        "category": "style",
        "severity": "low",
        "verbatim_excerpt": "if foo: bar()",
        "description": "Single-line conditional could be expanded.",
    }

    # Helper agrees.
    assert _validate_verbatim(finding, file_content) is True

    result = asyncio.run(
        validate_findings([finding], "indented.py", file_content)
    )
    assert len(result) == 1, "whitespace-drift excerpt should be accepted"
    assert result[0].verbatim_excerpt == "if foo: bar()"
    assert result[0].category == "style"


# ---------------------------------------------------------------------------
# R4 — mixed batch: only the valid finding survives (G1)
# ---------------------------------------------------------------------------


def test_mixed_batch_preserves_valid_finding(caplog):
    """A batch with one slop finding and one real finding keeps only the real one."""
    file_content = "x = None\ny = compute()\n"
    findings = [
        # Slop: claims a magic number that isn't in the file.
        {
            "line_start": 1,
            "line_end": 1,
            "category": "maintainability",
            "severity": "low",
            "verbatim_excerpt": "MAGIC = 42",
            "description": "Magic number.",
        },
        # Valid: 'x = None' really is on line 1.
        {
            "line_start": 1,
            "line_end": 1,
            "category": "bug",
            "severity": "high",
            "verbatim_excerpt": "x = None",
            "description": "Null pointer risk.",
        },
    ]

    with caplog.at_level("WARNING"):
        result = asyncio.run(
            validate_findings(findings, "mixed.py", file_content)
        )

    # Only the real finding survives.
    assert len(result) == 1, "exactly the valid finding should survive"
    assert result[0].description == "Null pointer risk."
    assert result[0].verbatim_excerpt == "x = None"

    # And the slop finding produced a WARNING.
    warnings = [
        r.getMessage()
        for r in caplog.records
        if r.levelname == "WARNING" and "not found in cited range" in r.getMessage()
    ]
    assert warnings, "expected WARNING for the dropped slop finding"
    assert any("mixed.py" in m for m in warnings)
    assert any("MAGIC = 42" in m for m in warnings), (
        "WARNING must include the rejected excerpt"
    )


# ---------------------------------------------------------------------------
# P1-P4 — real-code positive cases (plan Validation item 2).
#
# Validation item 2 of docs/superpowers/plans/2026-05-09-reviewer-grounding.md:
# "Run the pipeline against reviews known to be correct ... The findings must
#  round-trip without being rejected by the validator. If they're rejected,
#  the validator is too strict (whitespace normalization wrong, line-range
#  off-by-one, etc.)."
#
# The May-3 retro never snapshotted raw model responses, and the pre-grounding
# .ollama_reviews/ captures predate the verbatim_excerpt field, so there is no
# replayable real-history corpus. Instead these fixtures use VERBATIM real
# source frozen from ollama_sentinel/processor.py @ 47f1929 — the exact
# @retry construct the plan's own schema example cites — and assert correct
# findings on it survive the full validate_findings path. Each case targets
# a strictness failure mode item 2 names that R3 (zero-indent, 2-line) does
# not cover: multi-line spans, line-range boundaries, deep indentation, and
# regex-metachar excerpts (a guard against any future substring->regex swap).
#
# To regenerate after a real refactor: re-copy the cited line ranges from
# processor.py and update line_start/line_end. The uniqueness guard below
# fails loudly if an excerpt stops being unique to its cited range, so a
# stale fixture cannot pass for the wrong reason.
# ---------------------------------------------------------------------------

import re as _re


def _norm(text: str) -> str:
    """Mirror extractor._validate_verbatim's private whitespace normaliser."""
    return _re.sub(r"\s+", " ", text).strip()


# processor.py:112-117 — the @retry decorator block (multi-line, 4-space
# indent, parens). The plan's schema example cites this exact construct.
_RETRY_BLOCK = (
    "    @retry(\n"
    "        retry=retry_if_exception(_is_retryable_ollama_error),\n"
    "        wait=wait_exponential(multiplier=1, min=2, max=60),\n"
    "        stop=stop_after_attempt(5),\n"
    "        reraise=True,\n"
    "    )\n"
)

# processor.py:64-66 — contiguous real block used for both line-range
# boundary cases (first line and last line).
_STATUS_GUARD = (
    "    if isinstance(exc, httpx.HTTPStatusError):\n"
    "        status = exc.response.status_code\n"
    "        return status == 408 or status == 429 or status >= 500\n"
)

# processor.py:104-106 — deeply-indented httpx client init; line 2 is rich
# in regex metacharacters ( ( ) [ ] " . ).
_CLIENT_INIT = (
    "        self.client = httpx.AsyncClient(\n"
    "            timeout=httpx.Timeout(connect=5.0, read=float(config[\"request_timeout\"]), write=5.0, pool=5.0)\n"
    "        )\n"
)


@pytest.mark.parametrize(
    "case_id, file_content, finding",
    [
        # P1 — multi-line (>2) excerpt, real 4-space indentation, parens.
        (
            "P1_multiline_decorator",
            _RETRY_BLOCK,
            {
                "line_start": 1,
                "line_end": 6,
                "category": "reliability",
                "severity": "medium",
                "verbatim_excerpt": (
                    "@retry( retry=retry_if_exception(_is_retryable_ollama_error), "
                    "wait=wait_exponential(multiplier=1, min=2, max=60), "
                    "stop=stop_after_attempt(5), reraise=True, )"
                ),
                "description": "retry predicate spans the whole decorator block.",
            },
        ),
        # P2 — line-range END boundary: excerpt only on the final line.
        # An end-exclusive off-by-one would slice it away and reject.
        (
            "P2_end_boundary",
            _STATUS_GUARD,
            {
                "line_start": 3,
                "line_end": 3,
                "category": "maintainability",
                "severity": "low",
                "verbatim_excerpt": "return status == 408 or status == 429 or status >= 500",
                "description": "Status predicate is the last line of the block.",
            },
        ),
        # P3 — line-range START boundary: excerpt only on line 1.
        # A start-off-by-one (lines[start:end] with start=line_start) empties
        # the slice and rejects.
        (
            "P3_start_boundary",
            _STATUS_GUARD,
            {
                "line_start": 1,
                "line_end": 1,
                "category": "design",
                "severity": "low",
                "verbatim_excerpt": "if isinstance(exc, httpx.HTTPStatusError):",
                "description": "Guard clause is the first line of the block.",
            },
        ),
        # P4 — deep (12-space) indentation + regex metacharacters in the
        # excerpt. Whitespace must normalise; ( ) [ ] " must NOT be treated
        # as a pattern (guards a future substring->regex regression).
        (
            "P4_deep_indent_metachars",
            _CLIENT_INIT,
            {
                "line_start": 2,
                "line_end": 2,
                "category": "reliability",
                "severity": "medium",
                "verbatim_excerpt": (
                    'timeout=httpx.Timeout(connect=5.0, '
                    'read=float(config["request_timeout"]), write=5.0, pool=5.0)'
                ),
                "description": "Timeout construction line, deeply indented.",
            },
        ),
    ],
    ids=[
        "P1_multiline_decorator",
        "P2_end_boundary",
        "P3_start_boundary",
        "P4_deep_indent_metachars",
    ],
)
def test_correct_finding_on_real_source_is_not_rejected(
    case_id, file_content, finding, caplog
):
    """A correct finding on verbatim real source round-trips un-rejected.

    Empirical seal on plan Validation item 2: the validator must not be too
    strict against real, correct findings (whitespace, multi-line, line-range
    boundaries, metachars).
    """
    # Uniqueness guard: the normalised excerpt occurs exactly once in the
    # normalised file, so a True result can ONLY mean the cited range matched
    # — not an accidental substring elsewhere (green-for-wrong-reason).
    occurrences = _norm(file_content).count(_norm(finding["verbatim_excerpt"]))
    assert occurrences == 1, (
        f"{case_id}: excerpt must be unique to its cited range "
        f"(found {occurrences}); fixture is ambiguous and proves nothing"
    )

    # Step 1: the validator helper accepts it.
    assert _validate_verbatim(finding, file_content) is True, (
        f"{case_id}: validator rejected a correct excerpt — it is too strict"
    )

    # Step 2: the full path returns the finding intact, no WARNING.
    with caplog.at_level("WARNING"):
        result = asyncio.run(
            validate_findings([finding], f"{case_id}.py", file_content)
        )

    assert len(result) == 1, f"{case_id}: correct finding must survive validation"
    survived = result[0]
    assert survived.verbatim_excerpt == finding["verbatim_excerpt"]
    assert survived.category == finding["category"]
    assert survived.description == finding["description"]

    rejection_warnings = [
        r.getMessage()
        for r in caplog.records
        if r.levelname == "WARNING" and "not found in cited range" in r.getMessage()
    ]
    assert not rejection_warnings, (
        f"{case_id}: a correct finding must not emit a rejection WARNING; "
        f"got {rejection_warnings}"
    )
