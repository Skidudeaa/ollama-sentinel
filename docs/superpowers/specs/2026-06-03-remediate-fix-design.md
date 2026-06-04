# Remediate (`fix <id>`) — design

**Date:** 2026-06-03
**Status:** Approved (brainstorm), pending implementation plan
**Slice:** 3 of N in the "make findings actionable" arc (after surface/SARIF #14 and triage #15)

## Problem

Findings can be seen (surface) and closed (triage), but acting on one still
means reading the review and hand-editing the code. This slice closes the loop:
`fix <id>` asks the local model for a localized correction, shows a diff, and —
on explicit confirmation — writes it into the watched file and resolves the
finding. This is the **first code path that writes into watched source**, so the
design is built around containment, bounded blast radius, and never writing
unprompted.

## Scope

One command: `ollama-sentinel fix <finding_id>`. It relocates the finding by its
verbatim excerpt, generates a replacement for only that line span, previews a
unified diff, and applies it on confirmation, then resolves the finding as
`fixed` (no commit).

### Explicitly out of scope

- **Batch / multi-finding fix**, fixing by file or severity — single id only.
- **Auto-commit** — the change lands in the working tree; the user reviews,
  tests, and commits in their own git workflow (the post-commit hook then
  records the `fix_commit` Incident naturally).
- **Whole-file rewrites** and **model-emitted unified diffs** — rejected in
  favor of localized line-span replacement (see Decisions).
- **Reopen** of a finding; **stale-prune**; remediating an already-resolved
  finding (refused).

## Design

### Flow

```
fix <finding_id> [--yes]
  → _load_config_or_exit; resolve db_path; no-DB → red + exit 1
  → ViolationDB.get_finding(id)
        None            → "No finding with id N." exit 1
        resolved == 1   → "Finding N is already resolved." exit 1
  → safe_read(watch_dir / file_path, watch_dir)
        empty/unreadable → "Cannot read <file>." exit 1
  → relocate_finding(content, finding)        # from ollama_sentinel.sarif
        status != "relocated" → refuse (writing needs an excerpt-VERIFIED span):
            "stale"  → "Finding N: excerpt no longer in <file>;
                        cannot locate — nothing to fix." exit 1
            "stored" → "Finding N has no usable excerpt to locate by;
                        cannot fix safely." exit 1
        status == "relocated" → proceed with the verified [start..end]
  → propose_fix(content, finding, relocation, client, model_role="fix")
        → build_fix_prompt → client.generate_review("fix") → parse → splice
        status == "no_change" (spliced == original) →
              "Model proposed no change." exit 0 (no write, no resolve)
        status == "ok" → ProposedFix(new_content, start, end, old_text, new_text)
  → render unified diff (difflib.unified_diff) of old → new content
  → apply gate:
        --yes                         → apply without prompt
        TTY, no --yes                 → typer.confirm("Apply this fix to <file>?")
                                        'n'/default → "Aborted; finding N left open." exit 0
        non-TTY, no --yes             → print diff + "(preview only; pass --yes to apply)"
                                        exit 0, NO write
  → safe_write(watch_dir / file_path, new_content, watch_dir)   # atomic, contained
  → ViolationDB.mark_resolved(finding_id, resolution="fixed")   # no commit
  → green "Applied fix to <file>; finding N resolved (fixed)."
```

### Piece 1 — `safe_write` (`ollama_sentinel/utils.py`)

The write counterpart to `safe_read`, but it **raises** rather than degrading —
a failed or unsafe write must never appear to succeed.

```
safe_write(path: pathlib.Path, content: str, watch_dir: pathlib.Path) -> None
```

- Reject symlinks (`path.is_symlink()` → raise `ValueError`).
- Containment: `path.resolve()` must be under `watch_dir.resolve()`
  (`relative_to`, ValueError → raise `ValueError` "path traversal").
- **Atomic**: write to a temporary file in the same directory, then
  `os.replace(tmp, path)` (same-filesystem atomic rename). Clean up the temp on
  failure.
- Create parent directories if missing (within watch_dir).

### Piece 2 — fix-generation core (`ollama_sentinel/remediate.py`)

A cohesive module: pure helpers + one I/O orchestration.

- `splice_lines(content: str, start: int, end: int, replacement: str) -> str`
  *(pure)* — replace 1-based inclusive lines `[start..end]` with `replacement`,
  preserving every other line and clean line boundaries. Handles
  first-line/last-line spans and a file with or without a trailing newline. The
  replacement is normalized to sit on its own line(s) without doubling or
  dropping newlines at the seams.
- `parse_fix_response(raw: str) -> str` *(pure)* — strip a leading/trailing
  triple-backtick fence (with optional language tag) and surrounding blank
  lines if present; otherwise return `raw` unchanged. The model is instructed
  to emit bare code, but local models often fence anyway.
- `build_fix_prompt(content: str, start: int, end: int, finding: dict, ctx: int = 15) -> str`
  *(pure)* — a window of lines `[start-ctx .. end+ctx]` (clamped to the file),
  line-numbered, with the target span `[start..end]` clearly marked as the lines
  to replace; plus `[severity] category: description` and the
  `verbatim_excerpt`. Instruction: *return ONLY the corrected source for lines
  start–end, preserve surrounding indentation and style, output bare code with
  no markdown fences and no commentary; if you cannot fix it safely, return
  those lines unchanged.*
- `propose_fix(content, finding, relocation, client, model_role="fix") -> ProposedFix`
  *(I/O, async)* — `build_fix_prompt` → `client.generate_review(model_role, prompt)`
  (plain text, **no** JSON `response_format`) → `parse_fix_response` →
  `splice_lines`. Returns `ProposedFix`:
  - `status: str` — `"ok"` or `"no_change"` (new content equals original).
  - `new_content: str`, `start: int`, `end: int`,
  - `old_text: str` (the original span), `new_text: str` (the parsed
    replacement).

  Model role resolution mirrors `triage`: use `config.ollama.models["fix"]` if
  present, else fall back to `"default"`. (`generate_review` already falls back
  to `default` for an unknown role, so this is satisfied without config
  changes; a `fix` role is optional.)

### Piece 3 — `fix` CLI command (`ollama_sentinel/cli.py`)

Mirrors the existing command shape (`_load_config_or_exit`, `db_path`, no-DB
guard, `try/finally` DB close). It owns the relocation stale-guard, the diff
render, the apply gate, the `safe_write`, and `mark_resolved`. It constructs an
`OllamaClient` from `config.ollama` for `propose_fix` and closes it in
`finally`. `--yes/-y` is the only extra option beyond `--config/-c`.

- Diff: `difflib.unified_diff(old.splitlines(keepends=True),
  new.splitlines(keepends=True), fromfile=rel, tofile=rel)`, printed (Rich if
  TTY, plain otherwise).
- TTY detection: `sys.stdin.isatty()` (the codebase already uses an injectable
  `_is_stdin_tty()` for `triage` — reuse it).

## Data shapes

`get_finding(id)` returns a dict with `file_path` (relative to watch_dir),
`line_start`, `line_end`, `category`, `severity`, `description`,
`verbatim_excerpt`, `resolved`. `relocate_finding(content, finding)` returns a
`Relocation(start_line, end_line, status)` where status ∈
{relocated, stored, stale}. `propose_fix` consumes the relocated
`start_line`/`end_line`.

## Safety properties (the point of this slice)

- **Never writes unprompted** — TTY requires an interactive `[y/N]`; non-TTY
  requires explicit `--yes`; otherwise the command previews and writes nothing.
- **Bounded blast radius** — only the relocated `[start..end]` span is replaced;
  the rest of the file is spliced through untouched. The model never sees a
  mandate to rewrite the whole file.
- **Writes only to an excerpt-verified span** — only `relocate_finding` status
  `"relocated"` proceeds. A finding whose excerpt no longer locates (`"stale"`)
  OR has no usable excerpt to verify against (`"stored"` — empty excerpt, e.g.
  legacy/ungrounded findings) is refused. The command never splices into
  guessed or merely-stored line numbers.
- **Contained + atomic** — `safe_write` enforces watch_dir containment, refuses
  symlinks, and replaces the file atomically (no partial writes).
- **No surprise git** — the edit lands only in the working tree; resolution is
  recorded but nothing is committed.
- **No-op guard** — if the spliced result equals the original, nothing is
  written and the finding stays open.

## Testing

`tests/test_utils.py` (or a new `tests/test_safe_write.py`):
- containment: a path resolving outside watch_dir raises `ValueError`; traversal
  (`../`) raises.
- symlink target → raises.
- atomic round-trip: content written and read back equals input; a pre-existing
  file is replaced.

`tests/test_remediate.py`:
- `splice_lines`: single-line replace; multi-line replace; replace at first
  line; replace at last line (with and without trailing newline); indentation
  preserved; surrounding lines untouched.
- `parse_fix_response`: fenced (```lang ... ```) stripped to bare code; already
  bare passes through; leading/trailing blank lines trimmed.
- `build_fix_prompt`: includes the target line range, the finding
  severity/category/description, and the excerpt; context window clamps at file
  edges.
- `propose_fix` (mocked client): `ok` path splices the model's replacement;
  identical replacement → `no_change`; the client is called with plain text
  (no `response_format`).

`tests/test_cli.py`:
- happy path: seed a finding whose excerpt is in a real tmp file, mock the model
  to return a corrected span, invoke `fix <id> --yes`; assert the file content
  changed to the spliced result and the finding is `resolved=1,
  resolution='fixed'`.
- stale: finding excerpt absent from the file → exit 1, file unchanged, finding
  still open.
- empty-excerpt (`"stored"`) finding → exit 1, file unchanged (writing needs a
  verified span).
- missing id → exit 1; already-resolved id → exit 1.
- no-DB → exit 1.
- non-TTY without `--yes` → diff printed, file unchanged, finding still open,
  exit 0.
- no-change: model returns the original span → exit 0, file unchanged, finding
  still open.

## Decisions

- **Localized line-span replacement** over whole-file rewrite or model-emitted
  diffs. Bounds blast radius to the finding's span, keeps model output small
  (no truncation risk), produces a tight reviewable diff, and avoids the
  fragility of local models generating exact diff hunks. Relocation by excerpt
  gives the precise current span even after drift.
- **`safe_write` raises, `safe_read` degrades.** A read failure returning `""`
  is recoverable; a write that silently no-ops or escapes containment is not.
  The write primitive must fail loudly.
- **Interactive confirm + `--yes`; non-TTY previews.** The bare command never
  writes without either an interactive yes or an explicit `--yes`. Matches the
  arc's "human-in-the-loop, never silent writes" stance while staying scriptable.
- **Resolve on apply, no commit.** The `[y/N]` on the diff is the approval
  signal, so the finding is resolved `fixed`. Not committing keeps the change
  reviewable/revertable and lets the existing post-commit hook + Incident flow
  fire when the user commits.
- **Refuse already-resolved findings; `fix` model role with `default` fallback.**
  Both confirmed during brainstorm.

## Risk

Highest of the three slices — it writes into watched code. Mitigated by: the
preview/confirm gate (no unprompted writes), excerpt relocation (no writing to
stale lines), `safe_write` containment + atomicity, the bounded splice (rest of
file untouched), and the no-change/stale guards. The model output is the
least-controllable element; it is contained to the finding's span and always
shown as a diff before any write. Worst realistic case — a confirmed bad patch
— lands only in the working tree, reviewable and revertable, never committed.
