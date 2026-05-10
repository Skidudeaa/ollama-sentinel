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
