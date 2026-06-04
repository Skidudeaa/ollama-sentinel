# Remediate (`fix <id>`) Implementation Plan

> **For agentic workers:** Implement task-by-task with TDD (superpowers:test-driven-development). Steps use checkbox (`- [ ]`) syntax for tracking. The spec carries the full design — this plan is the sequencing + integration layer; do not duplicate signatures here, read them from the spec.

**Goal:** `ollama-sentinel fix <id>` — relocate a finding by its verbatim excerpt to an exact whole-line span, ask the local model for a localized replacement, preview a unified diff, and on confirmation write it into the watched file and resolve the finding `fixed`. First code path that writes into watched source.

**Spec:** `docs/superpowers/specs/2026-06-03-remediate-fix-design.md` (revised 2026-06-04 for safety).

**Tech stack:** Python 3.10+, SQLite (`ViolationDB`), Typer + Rich (CLI), `difflib`, `os.replace`/`shutil.copymode`, pytest (`tmp_path`, class-based, `CliRunner`).

**Branch:** `feat/remediate-fix` (the revised spec commit `1b79ef3` + this plan are its first commits).

**Stacking:** Four pieces, one PR each, stacked linearly. Each depends on the prior:
`0` relocation-exactness (sarif) → `1` write-primitives (utils) → `2` remediate core → `3` fix CLI.

---

## File structure

| File | Responsibility | Piece |
|------|----------------|-------|
| `ollama_sentinel/sarif.py` *(modify)* | add `Relocation.exact: bool`; set it on the exact vs word-fuzzy paths | 0 |
| `ollama_sentinel/utils.py` *(modify)* | `read_strict` (raises on non-UTF-8); `safe_write` (atomic, contained, UTF-8, mode-preserving, symlink-rejecting) | 1 |
| `ollama_sentinel/remediate.py` *(new)* | `splice_lines`, `parse_fix_response`, `build_fix_prompt` (pure); `propose_fix` (I/O); `ProposedFix` | 2 |
| `ollama_sentinel/cli.py` *(modify)* | `fix` command: load → read_strict → relocate (exact-only) → propose → diff → gate → re-stat → safe_write → mark_resolved | 3 |
| `tests/test_sarif.py` *(modify)* | `relocate_finding` exact vs fuzzy `exact` flag | 0 |
| `tests/test_utils.py` *(modify)* | `read_strict` strict-decode; `safe_write` containment/symlink/atomic/mode | 1 |
| `tests/test_remediate.py` *(new)* | `splice_lines`, `parse_fix_response`, `build_fix_prompt`, `propose_fix` | 2 |
| `tests/test_cli.py` *(modify)* | `fix` happy/stale/stored/fuzzy/non-utf8/mode/toctou/no-change/missing/already-resolved/non-TTY/no-DB | 3 |
| `CLAUDE.md` + `README.md` *(modify)* | document `fix` | docs |

---

## Piece 0: relocation exactness (PR 0) — touches SHIPPED code

`relocate_finding` returns `status="relocated"` from both the exact whole-line block match and the fuzzy word-sequence fallback. The write path must distinguish them.

- [ ] **RED:** in `tests/test_sarif.py`, assert an exact whole-line excerpt → `Relocation.exact is True`; a newline-flattened excerpt that only matches by word sequence → `status == "relocated"` and `exact is False`; `stored`/`stale` → `exact is False`.
- [ ] **GREEN:** add `exact: bool = False` to the `Relocation` dataclass; return `exact=True` on the block-match path, `exact=False` on the word-sequence + stored/stale paths.
- [ ] **Regression:** run the full sarif/surface suite (`pytest tests/test_sarif.py tests/test_cli.py -k "sarif or surface or Surface" -q`) — the field is additive; `build_sarif`/`generate_sarif_file` and their tests must stay green.

## Piece 1: write primitives (PR 1, stacked on 0)

- [ ] **RED:** in `tests/test_utils.py` — `read_strict` raises on a non-UTF-8 file and round-trips a UTF-8 file (symlink/`..` raise like `safe_read`); `safe_write` rejects symlinks, rejects traversal/outside-watch_dir (`ValueError`), round-trips + replaces atomically, and preserves a `0o755` file's mode.
- [ ] **GREEN:** implement `read_strict` (containment as `safe_read`, `encoding="utf-8"`, `errors="strict"`, raises) and `safe_write` (symlink reject → containment check → parent mkdir → temp in same dir, `encoding="utf-8"` → `shutil.copymode(path, tmp)` when `path` exists → `os.replace`, cleanup tmp on failure). Containment check precedes any mkdir.

## Piece 2: remediate core (PR 2, stacked on 1)

- [ ] **RED:** in `tests/test_remediate.py` — `splice_lines` (single/multi-line, first/last line, no-trailing-newline, indentation preserved, surrounding lines untouched); `parse_fix_response` (matched wrapping fence stripped, bare passes through, unmatched fence NOT stripped, blank edges trimmed); `build_fix_prompt` (target range + severity/category/description + excerpt present; window clamps at file edges); `propose_fix` with a mocked client (ok splices replacement; identical replacement → `no_change`; client called with plain text, no `response_format`).
- [ ] **GREEN:** implement `ollama_sentinel/remediate.py` per spec Piece 2 — pure helpers + `propose_fix` (async, `model_role="fix"`, returns `ProposedFix`).

## Piece 3: `fix` CLI (PR 3, stacked on 2)

- [ ] **RED:** in `tests/test_cli.py` — happy path (`--yes` splices + resolves `fixed`, file content changed); stale → exit 1 file unchanged finding open; stored (empty excerpt) → exit 1; fuzzy-only relocation → exit 1; non-UTF-8 target → exit 1; mode preserved (`0o755` stays `0o755`); file-changed-between-read-and-write → exit 1 no write; no-change → exit 0 file unchanged finding open; missing id → exit 1; already-resolved → exit 1; non-TTY without `--yes` → diff printed, no write, exit 0; no-DB → exit 1. Mock the model via the established client-mock pattern.
- [ ] **GREEN:** implement the `fix` command per spec Flow — `_load_config_or_exit`, db_path + no-DB guard, `get_finding` (None/resolved guards), `read_strict` + capture mtime/size, `relocate_finding` (proceed only on `status=="relocated" and exact`), `OllamaClient` from `config.ollama` + `propose_fix` (close in `finally`), always-print diff, apply gate (`--yes`/TTY confirm/non-TTY preview via `_is_stdin_tty()`), re-stat TOCTOU guard, `safe_write`, `mark_resolved(resolution="fixed")`.

## Docs (with PR 3)

- [ ] Add `fix` to the CLAUDE.md command list + `ollama_sentinel/cli.py` row, and to README.

---

## Verification

- [ ] Each piece: watch its tests fail RED, then GREEN; full suite green after each.
- [ ] The three safety properties have explicit regression tests (non-UTF-8 refused; mode preserved; fuzzy-only refused).
- [ ] After Piece 0, shipped sarif/surface behavior unchanged.
- [ ] Final adversarial review pass over the full `fix` diff (correctness + write-path safety) before merge.
