# 2026-11-02 (session 2) — fracture-review of the bake-fix arc, 10 defects fixed, plain-language manual

Follows `2026-11-02-bake-fixes-and-active-workflow-handoff.md` (commits
`0088785`..`9100362`). This session reviewed that arc with `/fracture-review`,
fixed every finding, re-reviewed each fix commit until a pass came back CLEAR,
and rewrote `docs/GUIDE.md` as a step-by-step manual.

## State at end of session

- `master` == `origin/master`, working tree clean (this note's commit on top of `1f49fe9`).
- Suite: `pytest tests/ -q` -> 881 passed / 16 skipped at `1f49fe9` (run it; do not trust this number later).
- Final review of `1f49fe9` vs `64fba60`: **CLEAR** (0 findings, 0 gaps).
- Pre-existing stash `stash@{0}` ("safety: chore/remove-serena-mcp prep 2025-08-10") untouched.
- Local-only change: `.omc/` appended to `.git/info/exclude` (OMC hook state rewrites
  files on every tool call and broke the review's frozen fingerprint). Not tracked.
- Project is still parked: no watchers, hooks, or droplet units were started.

## Commits this session

| Commit | What |
|---|---|
| `e696eee` | Five defects from review #1 (below, F1-F5). |
| `867440c` | Two regressions review #2 found in `e696eee` (R1, R2). |
| `64fba60` | Two defects review #3 found in `867440c` (R3, R4). |
| `1f49fe9` | One defect review #4 found in `64fba60` (R5). Review #5: CLEAR. |
| (this commit) | `docs/GUIDE.md` rewrite, README pointer, this note, CLAUDE.md landing entry. |

## Defects fixed (each has a regression test that fails on the prior source)

### Review #1 — target `0088785..9100362` (base `03ca9c7`)

- **F1 reconcile closed validated findings on unchanged files.** `validate_findings`
  admitted sub-token excerpts (`password = "x"` cited from `db_password = "x"`);
  `relocate_finding` matches whole lines/tokens, so `reconcile_file_findings`
  (runs before every review in `watcher.process_change`) closed them as `stale`,
  and a re-flag reset `occurrence_count` to 1. Final fix is R1 below.
- **F2 `check --fail-on` failed open.** `process_change` swallows review errors;
  `check` printed "Check complete — no open findings" and exited 0 with Ollama down.
  Now `process_change -> bool` (False only when review/save raised; skips return True)
  and `check` exits 1 with "Review failed for N file(s): ..." (`cli.py`, `watcher.py`).
  **Behavior change:** the `--pre-commit` hook blocks commits while Ollama is down
  (`git commit --no-verify` bypasses). Also exits 1 on review failure without `--fail-on`.
- **F3 `repair` backfilled excerpts from stale line numbers.** Excerpt-less rows are
  never relocated, so slicing current file text at stored lines anchored findings to
  unrelated code with `exact=True` (defeats `fix`'s span guard). Now backfill only when
  file mtime <= `last_seen` (`violation_db._unchanged_since`). On the August bake DBs
  this means very few backfills — intended.
- **F4 Control Center retargeted actions after refresh.** Detail view and r/d/c/f/y
  resolved the target by selection index into a re-ranked list; a watcher
  `occurrence_count` bump swapped the finding (probe: `d` dismissed Y while X was open;
  `y` wrote X's fix under Y's header). Now the Patterns cursor is pinned by finding id
  across refreshes (`dashboard._ranked_patterns/_selected_pattern_id/_repin_selection`);
  if the pinned finding disappears while its detail is open, detail drops to NORMAL with
  "Finding #N is no longer open."
- **F5 Swift structural recall linked unrelated files** via shared nested names
  (`CodingKeys` in every Codable model). Final fix is R2 below.

### Review #2 — target `e696eee`

- **R1 reconcile substring gate kept fixed findings open** (`eval(expr)` survives inside
  `ast.literal_eval(expr)`). Token-boundary rules cannot separate this from F1's case, so
  the root cause was fixed upstream: `validate_findings` now stores the whole source
  line(s) containing the model's excerpt (`extractor._expand_to_whole_lines`), and the
  reconcile gate was removed — `relocate_finding` alone decides staleness, so reconcile,
  `prune`, and `surface` agree. **Behavior change:** stored excerpts are raw source lines,
  not model text. Legacy rows holding sub-token fragments close once on next save.
  `tests/test_grounding_regression.py` excerpt-equality assertions were updated to the new
  contract (stored excerpt is real file text containing the model's excerpt).
- **R2 brace-depth Swift filter was string-blind** — a `{` in a string literal dropped every
  later top-level type (theater6: 134 lost, incl. `NewsViewModel`). Top-level now means the
  declaring line is unindented. theater6: 0 top-level decls dropped vs a string-aware oracle;
  type map 738 -> 568 names (170 removed are all nested).

### Review #3 — target `867440c`

- **R3 blank-line excerpts never expanded** (per-line normalize + join left a double space),
  so the raw excerpt was stored and reconcile closed it. **R4 expansion was cubic** in the
  cited range inside async `validate_findings` (3.1s at 800 lines). Fix: normalize the whole
  window once with a per-char line map; one `str.find`; restore the prefix-strip fallback.

### Review #4 — target `64fba60`

- **R5 first match, not smallest span.** An excerpt whose tokens also occurred earlier across a
  line break anchored to fragments of the wrong lines. Now scans all occurrences, keeps the
  fewest-lines span (earliest on ties), stops at the first single-line match.
  Verified: brute-force oracle 5,598 cases 0 mismatches; relocation fuzz 32,549 accepted
  0 bad; 200k-line worst cases < 0.1s.

## Manual rewrite

- `docs/GUIDE.md` is now a 15-section task manual in ASD-STE100 style (operator request:
  <= 20 words per instruction sentence, <= 25 per description, one instruction per
  sentence, warnings before the step). Every command/flag was taken from live `--help`
  output or source. A mechanical length check reported 0 violations.
- Removed from GUIDE: philosophy, project file tree, research-agent internals and sample
  impact output, guardrail scoring internals. Design/philosophy now pointed to
  `docs/VISION.md` and `docs/index.html`; file tree remains in `CLAUDE.md`.
  Not verified that every removed detail exists elsewhere.
- Two usability facts surfaced while writing it (both pre-existing behavior):
  - Every command reads `ollama-sentinel.yaml` from the current directory.
  - The Patterns panel hides findings seen once (`dashboard --min-count` default 2);
    `ollama-sentinel dashboard -n 1` shows all. Focus starts on Overview; Tab twice
    reaches Patterns, where r/d/c/f work.
- The `dashboard` command's help string still says "(read-only)", which is wrong since the
  r/d/c/f/y actions landed. Not fixed.

## Known, verified, NOT fixed (judged non-material or out of scope)

From the reviewers' reports; each was reproduced by a probe but not recorded as a finding:

- JS/TS `_JS_IMPORT_RE` first branch lazily spans `import './a'; import './b'; import {u} from './u'`
  and drops the side-effect imports (`import_resolver.py` ~line 41).
- JS/TS export-heavy files scan quadratically (4,000 exports ~6.5s); not visible on real repos
  (physioTheater 310 files 0.74s total).
- A `.swift` save clears the whole resolver cache (2 full walks/save, ~0.6s on theater6); any file
  outside the scan set (e.g. under `.github/`) triggers one full walk per save.
- The resolver walks `.claude/worktrees` and `.omc` copies that the watcher ignores (theater6:
  69% of Swift declarations come from those copies; neighbors include duplicate paths).
- Dashboard `r`/`d` call `mark_resolved` without the CLI's still-open check, so a finding already
  closed `stale` can be relabeled `fixed`, and a missing id reports success.
- `process_change` returns True when only persistence failed (best-effort by design), so `check`
  passes in that case.
- `review` (single-file) ignores `process_change`'s result and exits 0 when the review failed.

## Evidence locations

- Fracture-review ledgers (outside repo, `~/.cache/fracture-review/ollama-sentinel-bbfa452630/`):
  `a341e24cd82ed4bf` (review #1), `7dfa16bc7393589c` (#2), `e5d613cf4253fb49` (#3),
  `59dc1109b0d9393f` (#4), `da88839cea516c63` (#5, CLEAR).
- Probe scripts lived in the session scratchpad under `/private/tmp/claude-501/...`; they are
  ephemeral. The durable proofs are the regression tests:
  `TestReconcileKeepsValidatorAdmittedFragments` (test_sarif), `TestRepairBackfillRequiresUnchangedFile`
  (test_violation_db), `TestCheckFailsClosed` (test_cli), `TestSelectionPinnedAcrossRefresh`
  (test_dashboard), `TestSwiftNestedTypeNames` (test_import_resolver), `TestExpandToWholeLinesCost`
  (test_extractor).

## Resume here

1. `git status` clean on `master`; `pytest tests/ -q` green.
2. Project remains parked. If reviving, read `2026-08-25-bake-verdict-and-fleet-shutdown.md` first,
   then `docs/GUIDE.md` for operation.
3. Before running `repair --yes` on the bake DBs, expect few excerpt backfills (F3 guard) — that is
   correct, not a bug.
4. Cheapest next fixes if wanted: dashboard help string "(read-only)"; `review` exit code on failure;
   dashboard r/d still-open check; resolver skip of `.claude/`/`.omc/`.
