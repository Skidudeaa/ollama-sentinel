# Remediate (`fix <id>`) — design

**Date:** 2026-06-03 (revised 2026-06-04 after adversarial readiness review)
**Status:** Approved (brainstorm), revised for safety; implementation plan written (`docs/superpowers/plans/2026-06-03-remediate-fix.md`)
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
verbatim excerpt **to an exact, whole-line-aligned span**, generates a
replacement for only that line span, previews a unified diff, and applies it on
confirmation, then resolves the finding as `fixed` (no commit). A finding that
only relocates by the fuzzy word-sequence fallback (it can land mid-line) is
refused — see Decisions.

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
  → read_strict(watch_dir / file_path, watch_dir)   # raises, never degrades
        unreadable / not UTF-8 → "Cannot read <file> as UTF-8; refusing to edit
                                  (would corrupt non-text bytes)." exit 1
        capture mtime+size now for the pre-write TOCTOU check
  → relocate_finding(content, finding)        # from ollama_sentinel.sarif
        not an EXACT relocation → refuse (writing needs a whole-line-VERIFIED span):
            "stale"             → "Finding N: excerpt no longer in <file>;
                                   cannot locate — nothing to fix." exit 1
            "stored"            → "Finding N has no usable excerpt to locate by;
                                   cannot fix safely." exit 1
            relocated but fuzzy → "Finding N: excerpt only matches across line
                                   boundaries; cannot fix safely (would clobber
                                   surrounding code)." exit 1
        exact "relocated" (reloc.exact) → proceed with the verified whole-line [start..end]
  → propose_fix(content, finding, relocation, client, model_role="fix")
        → build_fix_prompt → client.generate_review("fix") → parse → splice
        status == "no_change" (spliced == original) →
              "Model proposed no change." exit 0 (no write, no resolve)
        status == "ok" → ProposedFix(new_content, start, end, old_text, new_text)
  → render unified diff (difflib.unified_diff) of old → new content — ALWAYS,
    including under --yes (the printed diff is the record of what was applied)
  → apply gate:
        --yes                         → print diff, then apply
        TTY, no --yes                 → print diff + typer.confirm("Apply this fix to <file>?")
                                        'n'/default → "Aborted; finding N left open." exit 0
        non-TTY, no --yes             → print diff + "(preview only; pass --yes to apply)"
                                        exit 0, NO write
  → re-stat watch_dir / file_path; if mtime/size changed since the read →
        "<file> changed since it was read; re-run fix." exit 1 (NO write)
  → safe_write(watch_dir / file_path, new_content, watch_dir)
        # atomic, contained, UTF-8, preserves the file's mode
  → ViolationDB.mark_resolved(finding_id, resolution="fixed")   # no commit
  → green "Applied fix to <file>; finding N resolved (fixed)."
```

### Piece 0 — relocation exactness (`ollama_sentinel/sarif.py`)

`relocate_finding` currently returns `status == "relocated"` from *two* paths:
the exact whole-line block match (`file_lines[i:i+n] == excerpt_lines`) and the
fuzzy word-sequence fallback (for newline-flattened excerpts). The write path
must tell them apart, so add one additive field to `Relocation`:

```
@dataclass
class Relocation:
    start_line: int
    end_line: int
    status: str          # "relocated" | "stored" | "stale"
    exact: bool = False  # True only on the whole-line block match
```

Set `exact=True` on the block-match return and `exact=False` on the
word-sequence return (and on `stored`/`stale`). `status` semantics are
unchanged, so the existing `surface` / `generate_sarif_file` caller and its
tests keep working — SARIF still emits every non-stale relocation; the field is
purely additive. Only `fix` consumes `exact`. This is the one piece that touches
**shipped** code with existing tests; run the sarif/surface suite after it.

### Piece 1 — write primitives (`ollama_sentinel/utils.py`)

Two counterparts to `safe_read`. Both **raise** rather than degrading — on the
write path a silent failure or a lossy round-trip must never appear to succeed.
(`safe_read`'s `errors="replace"` is correct for review, but writing back a
replace-decoded string would corrupt any non-UTF-8 bytes in untouched regions.)

```
read_strict(path: pathlib.Path, watch_dir: pathlib.Path) -> str
safe_write(path: pathlib.Path, content: str, watch_dir: pathlib.Path) -> None
```

`read_strict` — same symlink + `relative_to` containment as `safe_read`, but
reads with `encoding="utf-8"`, `errors="strict"`: a non-UTF-8 file raises
(`UnicodeDecodeError`) so `fix` refuses it rather than mangling untouched bytes
on write-back.

`safe_write`:
- Reject symlinks (`path.is_symlink()` → raise `ValueError`).
- Containment: `path.resolve()` must be under `watch_dir.resolve()`
  (`relative_to`, ValueError → raise `ValueError` "path traversal"). The
  containment check runs **before** any directory creation.
- **Atomic**: write (`encoding="utf-8"`) to a temporary file in the same
  directory, then `os.replace(tmp, path)` (same-filesystem atomic rename). Clean
  up the temp on failure.
- **Preserve mode**: when `path` already exists, `shutil.copymode(path, tmp)`
  before the replace, so a `0o755` file is not silently flattened to the temp's
  `0o600`. (`os.replace` carries the inode, not the old name's mode.)
- Create parent directories if missing (within watch_dir) — after the
  containment check.

### Piece 2 — fix-generation core (`ollama_sentinel/remediate.py`)

A cohesive module: pure helpers + one I/O orchestration.

- `splice_lines(content: str, start: int, end: int, replacement: str) -> str`
  *(pure)* — replace 1-based inclusive lines `[start..end]` with `replacement`,
  preserving every other line and clean line boundaries. Handles
  first-line/last-line spans and a file with or without a trailing newline. The
  replacement is normalized to sit on its own line(s) without doubling or
  dropping newlines at the seams.
- `parse_fix_response(raw: str) -> str` *(pure)* — strip a fence **only when a
  triple-backtick opener on the first non-blank line is matched by a closing
  fence on the last non-blank line** (optional language tag on the opener),
  trimming surrounding blank lines; otherwise return `raw` unchanged. Requiring
  a matched wrapping pair avoids corrupting a legitimately fenced `.md` target
  or a Python docstring whose real content contains a fence. The model is
  instructed to emit bare code, but local models often fence anyway.
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
  The unified diff is always printed before any write, including under `--yes`.
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
- **Encoding integrity** — the file is read with `errors="strict"`; a non-UTF-8
  file is refused, never round-tripped through `errors="replace"` (which would
  corrupt untouched bytes). `safe_write` writes UTF-8 explicitly.
- **Exact span only** — `fix` proceeds only on an `exact` whole-line relocation.
  A fuzzy word-sequence match (which can land mid-line) is refused, so the
  bounded splice never clobbers unrelated code sharing a line with the excerpt.
- **Mode preserved** — the replaced file keeps its original permission bits; an
  executable script stays executable.
- **No stale-read clobber** — the file is re-stat'd immediately before the
  write; if it changed since it was read (editor save, watcher rewrite), the fix
  aborts without writing.

## Testing

`tests/test_utils.py` (or a new `tests/test_safe_write.py`):
- `read_strict`: a non-UTF-8 file raises (no lossy replacement); a valid UTF-8
  file round-trips; symlink and `../` traversal raise like `safe_read`.
- containment: a path resolving outside watch_dir raises `ValueError`; traversal
  (`../`) raises.
- symlink target → raises.
- atomic round-trip: content written and read back equals input; a pre-existing
  file is replaced.
- mode preserved: a pre-existing `0o755` file is still `0o755` after `safe_write`.

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

`tests/test_sarif.py` (Piece 0):
- `relocate_finding`: an exact whole-line excerpt → `exact=True`; a
  newline-flattened excerpt that only matches by word sequence → `status
  "relocated"`, `exact=False`; existing stale/stored cases unchanged.

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
- fuzzy-only relocation (excerpt matches only across line boundaries) → exit 1,
  file unchanged, finding still open.
- non-UTF-8 target file → exit 1, file unchanged, finding still open.
- mode preserved: a `0o755` target is still `0o755` after a successful fix.
- file changed between read and write (simulate an mtime/size change) → exit 1,
  no write, finding still open.

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
  reviewable/revertible and lets the existing post-commit hook + Incident flow
  fire when the user commits.
- **Refuse already-resolved findings; `fix` model role with `default` fallback.**
  Both confirmed during brainstorm.
- **Refuse fuzzy relocation; write only to exact whole-line spans.** An `exact`
  match is whitespace-normalized but whole-line-aligned, so replacing lines
  `[start..end]` is safe. The word-sequence fallback can match an excerpt that
  sits mid-line (e.g. excerpt `foo(bar)` inside `result = foo(bar) + baz`); its
  line span would pull in unrelated code, and a whole-line splice would clobber
  it. `surface` (read-only) keeps using fuzzy matches; only the write path
  refuses them. *(This is the most debatable call in the revision — the
  alternative is accept-fuzzy-with-warning. Chosen conservatively because this is
  the first write-to-source path; revisit if it refuses too many real findings.)*
- **Read strict, write UTF-8, preserve mode.** The write path reads with
  `errors="strict"` (refusing non-UTF-8 rather than corrupting it via
  `errors="replace"`), writes UTF-8 explicitly, and copies the original file's
  mode onto the replacement so permissions/executability survive `os.replace`.
- **Re-read before write (TOCTOU).** Model generation takes seconds; the target
  is re-stat'd just before `os.replace` and the fix aborts if mtime/size changed,
  so a concurrent editor save or watcher rewrite is never silently clobbered.
- **Always show the diff, including under `--yes`.** `--yes` skips the
  interactive prompt, not the preview; the printed diff is the record of exactly
  what landed.

## Risk

Highest of the three slices — it writes into watched code. Mitigated by: the
preview/confirm gate (no unprompted writes), excerpt relocation (no writing to
stale lines), `safe_write` containment + atomicity, the bounded splice (rest of
file untouched), and the no-change/stale guards. The model output is the
least-controllable element; it is contained to the finding's span and always
shown as a diff before any write. Worst realistic case — a confirmed bad patch
— lands only in the working tree, reviewable and revertible, never committed.
