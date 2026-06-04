"""Localized fix generation for ``ollama-sentinel fix <id>``.

Pure helpers (``splice_lines``, ``parse_fix_response``, ``build_fix_prompt``)
plus one I/O orchestration (``propose_fix``). The fix is bounded to the
finding's relocated line span — the model never sees a mandate to rewrite the
whole file, and the rest of the file is spliced through untouched.
"""
from dataclasses import dataclass


def splice_lines(content: str, start: int, end: int, replacement: str) -> str:
    """Replace 1-based inclusive lines ``[start..end]`` with ``replacement``.

    Every other line is preserved verbatim. The replacement is normalized onto
    its own line(s): internal newlines are kept, and a single trailing newline
    is present iff the original last replaced line ended with one — so a file
    with or without a final newline keeps that property, and seams never double
    or drop a newline.
    """
    lines = content.splitlines(keepends=True)
    n = len(lines)
    start = max(1, start)
    end = min(end, n)

    prefix = lines[: start - 1]
    target = lines[start - 1 : end]
    suffix = lines[end:]

    orig_had_newline = bool(target) and target[-1].endswith(("\n", "\r"))
    repl_norm = "\n".join(replacement.splitlines())
    if orig_had_newline:
        repl_norm += "\n"

    return "".join(prefix) + repl_norm + "".join(suffix)


def parse_fix_response(raw: str) -> str:
    """Strip a markdown fence only when it *wraps the entire output*.

    A fence is removed only when the first non-blank line is a triple-backtick
    opener (with an optional language tag) and the last non-blank line is a bare
    triple-backtick closer. Otherwise ``raw`` is returned unchanged — so a
    legitimately fenced ``.md`` target, a docstring containing a fence, or a
    dangling half-fence is never corrupted.
    """
    lines = raw.splitlines()
    non_blank = [i for i, ln in enumerate(lines) if ln.strip()]
    if len(non_blank) < 2:
        return raw

    first, last = non_blank[0], non_blank[-1]
    opener = lines[first].strip().startswith("```")
    closer = lines[last].strip() == "```"
    if opener and closer:
        return "\n".join(lines[first + 1 : last])
    return raw


def build_fix_prompt(
    content: str, start: int, end: int, finding: dict, ctx: int = 15
) -> str:
    """Build the plain-text prompt asking the model to rewrite lines start-end.

    Shows a line-numbered window ``[start-ctx .. end+ctx]`` (clamped to the
    file) with the target lines marked, plus the finding's
    ``[severity] category: description`` and verbatim excerpt. Instructs the
    model to return bare code for only that span.
    """
    lines = content.splitlines()
    n = len(lines)
    win_start = max(1, start - ctx)
    win_end = min(n, end + ctx)

    rendered = []
    for ln in range(win_start, win_end + 1):
        marker = ">>" if start <= ln <= end else "  "
        rendered.append(f"{marker} {ln:>5} | {lines[ln - 1]}")
    window = "\n".join(rendered)

    severity = finding.get("severity", "")
    category = finding.get("category", "")
    description = finding.get("description", "")
    excerpt = finding.get("verbatim_excerpt", "")

    return (
        "You are fixing a single code finding.\n\n"
        f"Finding: [{severity}] {category}: {description}\n"
        f"Excerpt:\n{excerpt}\n\n"
        f"File around the finding — lines marked >> (lines {start}-{end}) are "
        "the ones to replace:\n\n"
        f"{window}\n\n"
        f"Return ONLY the corrected source for lines {start}-{end}, preserving "
        "the surrounding indentation and style. Output bare code with no "
        "markdown fences and no commentary. If you cannot fix it safely, "
        "return those lines unchanged."
    )


@dataclass
class ProposedFix:
    """A model-proposed localized fix, before any write."""
    status: str  # "ok" | "no_change"
    new_content: str
    start: int
    end: int
    old_text: str
    new_text: str


async def propose_fix(
    content: str, finding: dict, relocation, client, model_role: str = "fix"
) -> ProposedFix:
    """Ask the model for a localized replacement for the relocated span.

    Plain-text generation (no ``response_format``): build the prompt, call the
    client, parse off any fence, splice into the file. ``status`` is
    ``"no_change"`` when the spliced result equals the original (the caller then
    writes nothing and leaves the finding open).
    """
    start, end = relocation.start_line, relocation.end_line
    prompt = build_fix_prompt(content, start, end, finding)
    raw = await client.generate_review(model_role, prompt)
    new_text = parse_fix_response(raw)
    new_content = splice_lines(content, start, end, new_text)
    old_text = "".join(content.splitlines(keepends=True)[start - 1 : end])
    status = "no_change" if new_content == content else "ok"
    return ProposedFix(
        status=status,
        new_content=new_content,
        start=start,
        end=end,
        old_text=old_text,
        new_text=new_text,
    )
