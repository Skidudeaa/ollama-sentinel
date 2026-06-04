# Surface findings as SARIF — design

**Date:** 2026-06-03
**Status:** Approved (brainstorm), pending implementation plan
**Slice:** 1 of N in the "make findings actionable" arc

## Problem

Ollama Sentinel generates findings, dedupes them in `ViolationDB`, and
corroborates a subset into Incidents. Today those findings drive exactly **one**
thing: they are fed back into future review prompts as "prior violations"
(`_get_ranked_prior_violations` → semantic / structural / single-file recall).

They drive **zero developer-facing action**. There is no way to:

- see a finding at its location in the editor,
- act on a finding (fix it),
- or even *close* a finding — `ViolationDB.mark_resolved` exists but no CLI verb
  calls it.

Findings accumulate in SQLite and dead-end in `.ollama_reviews/*.md`. This spec
covers the cheapest, lowest-risk first step toward closing that loop: **surfacing
open findings where the developer edits**, as a standard SARIF artifact.

## Scope

A `surface` command plus watcher auto-refresh that serializes the open findings
in `ViolationDB` to `.ollama_reviews/findings.sarif` (SARIF 2.1.0), with each
finding re-located to its *current* line by `verbatim_excerpt`.

The artifact is consumed by:

- **VS Code / Cursor** via the SARIF Viewer extension → Problems panel,
  click-to-jump.
- **GitHub code scanning** in CI via `github/codeql-action/upload-sarif` →
  Security tab + inline PR annotations.

### Explicitly out of scope (later slices)

- **Triage verbs** (`resolve` / `dismiss`) — the slice that wires
  `mark_resolved` to the CLI.
- **Auto-resolving stale findings** — `surface` reports stale findings but never
  mutates DB state. Auto-resolve belongs to the triage slice.
- **Remediation** (`fix <id>` — model proposes a patch, applies on confirm).
- **LSP diagnostics daemon** and **inline `# sentinel:` comments** — rejected
  mechanisms (see "Decisions").

## Design

### Module: `ollama_sentinel/sarif.py`

One cohesive module: a pure core (relocation + SARIF construction) and a thin
I/O orchestration entry. The split keeps the SARIF logic unit-testable with
in-memory data and isolates filesystem/DB access to one function.

#### `relocate_finding(content: str, finding: dict) -> Relocation` *(pure)*

Re-anchors a finding to its current position. Findings store line numbers as of
review time; those numbers drift as the file edits, so we re-locate by the
verbatim excerpt rather than trusting `line_start` / `line_end`.

`Relocation` is a small dataclass: `start_line: int`, `end_line: int`,
`status: str` where `status ∈ {"relocated", "stored", "stale"}`.

Algorithm:

1. **Empty excerpt** (legacy / ungrounded findings often have none) → cannot
   relocate. Return stored `line_start` / `line_end` with `status="stored"`.
2. Search `content` for the excerpt as a substring/line block.
   - **Found exactly once** → use that line. `status="relocated"` (or `"stored"`
     if it coincides with the stored line — cosmetic, treat as relocated).
   - **Found multiple times** → pick the occurrence whose start line is nearest
     to stored `line_start`. `status="relocated"`.
   - **Not found** → `status="stale"`.

Matching normalizes leading/trailing whitespace per line so reindentation does
not produce false staleness. The excerpt is matched against raw file lines (the
file is read without the prompt's `N: ` numbering).

#### `build_sarif(located_findings, *, tool_version, corroborated_ids) -> dict` *(pure)*

Builds a SARIF 2.1.0 document from findings that already carry a `Relocation`.

- `version: "2.1.0"`, `$schema` set to the SARIF 2.1.0 schema URL.
- `runs[0].tool.driver`:
  - `name: "ollama-sentinel"`, `version: tool_version`,
    `informationUri: "https://github.com/Skidudeaa/ollama-sentinel"`.
  - `rules[]`: one rule per distinct `category`, deduped. Each rule:
    `id` (e.g. `"ollama-sentinel/bug"`), `name`, `shortDescription.text`, and a
    `defaultConfiguration.level` from the severity→level mapping below.
- `runs[0].results[]`: one per finding.
  - `ruleId` = `"ollama-sentinel/<category>"`.
  - `level` from severity:
    - `critical` → `"error"`
    - `high` → `"error"`
    - `medium` → `"warning"`
    - `low` → `"note"`
    - unknown / missing → `"warning"`
  - `message.text` = `description`.
  - `locations[0].physicalLocation`:
    - `artifactLocation.uri` = `file_path` (already stored relative to
      `watch_dir`), `uriBaseId: "SRCROOT"`.
    - `region.startLine` / `region.endLine` from the `Relocation`.
    - `region.snippet.text` = `verbatim_excerpt` (omitted when empty).
  - `partialFingerprints`: `{"ollamaSentinel/v1": <sha256 hex>}` where the hash
    is over `(file_path, category, normalized excerpt)` — **never line numbers**,
    so code scanning tracks a finding across commits despite drift. When the
    excerpt is empty, fall back to hashing `(file_path, category, description)`.
  - `properties`: `{severity, occurrence_count, corroborated: bool,
    relocation: status}`. `corroborated` is `True` when the finding id is in
    `corroborated_ids`.

#### `generate_sarif_file(db, watch_dir, output_dir, *, tool_version) -> SurfaceSummary` *(I/O)*

Orchestration entry, the only function that touches the DB and filesystem.

1. `rows = db.get_all_unresolved()`.
2. `corroborated_ids = {f["id"] for f in db.get_findings_with_incidents(distinct_paths)}`
   — one query, marks findings that already have ≥1 Incident.
3. Group rows by `file_path`. For each file, read current content via
   `safe_read(watch_dir / file_path, watch_dir)` for containment. A file that no
   longer exists (or fails containment) → all its findings are `stale`.
4. `relocate_finding` each row against its file content.
5. **Exclude `stale` findings from the SARIF results** (don't surface ghosts);
   count them. `stored` (empty-excerpt) and `relocated` findings are emitted.
6. `build_sarif(...)` → write `output_dir / "findings.sarif"` (pretty JSON).
7. Return `SurfaceSummary` dataclass: `emitted: int`, `relocated: int`,
   `stale: int`, `unverified: int` (empty-excerpt → stored), `path: Path`.

`surface` is strictly read-only: it does **not** call `mark_resolved` on stale
findings, does not edit source, and writes only the one SARIF artifact.

### CLI: `ollama-sentinel surface`

Mirrors the `report` / `incidents` command shape in `cli.py`:

```
ollama-sentinel surface [-c CONFIG] [-o OUTPUT]
```

- Resolve config via `_load_config_or_exit`.
- Resolve `db_path = watch_dir / config.memory.db_path`; friendly
  `[yellow]No violation database found…[/]` message + exit if absent (matches
  `report` / `incidents`).
- Default output is `watch_dir / config.output.directory / findings.sarif`;
  `-o/--output` overrides the SARIF path.
- Open `ViolationDB`, call `generate_sarif_file`, close DB, print a summary line:
  `Wrote 7 findings → findings.sarif (5 relocated, 2 stale)`.

`tool_version` is `ollama_sentinel.__version__`.

### Watcher auto-refresh

After the existing best-effort persist block in
`watcher.FileSentinel.process_change` (currently `watcher.py:263-269`, right
after `persist_findings`), regenerate the SARIF so the Problems panel stays live
while `run` is active:

- Call `generate_sarif_file(self.violation_db, self.processor.watch_dir,
  self.processor.output_dir, tool_version=__version__)` via
  `asyncio.to_thread`.
- **Best-effort**, gated on `self.violation_db` being set: wrap in
  `try/except`, log a warning on failure, never raise. This matches the
  project's "finding extraction and violation persistence never block review
  saving" convention — a SARIF write failure must not break the review.
- Runs whether or not *this* review produced new findings (a relocation may have
  changed because the file edited), but only when `violation_db` exists.

Performance note: each refresh re-reads every currently-flagged file to relocate
excerpts. At local single-developer repo scale the flagged-file set is small and
reads are OS-cached; full rebuild is chosen for correctness. If this ever
becomes hot, a later optimization can relocate only the just-reviewed file and
reuse stored lines elsewhere — out of scope here.

## Data shapes

`ViolationDB` rows are dicts with: `id`, `file_path` (relative to `watch_dir`),
`line_start`, `line_end`, `category`, `severity`, `description`,
`verbatim_excerpt` *(may be absent/empty on legacy rows)*, `occurrence_count`,
`resolved`, `embed_text`. `get_all_unresolved()` returns `resolved = 0` rows.

> **Required prerequisite — the excerpt is not persisted today.** The
> `Finding` dataclass has a `verbatim_excerpt` field, but the `findings` table
> has **no** `verbatim_excerpt` column and `persist_findings` never writes it
> (it inserts `file_path, line_start, line_end, category, severity, description,
> first_seen, last_seen, embed_text` — the excerpt is discarded). Relocation
> therefore cannot work on existing rows. The plan **must**, as its first piece:
> (1) add a nullable `verbatim_excerpt TEXT` column via an idempotent migration
> (same pattern as the `embed_text` backfill in `_migrate`), and (2) make
> `persist_findings` write `f.verbatim_excerpt`. Old rows and ungrounded findings
> keep an empty excerpt and degrade to `status="stored"` (emitted at stored
> lines, `properties.relocation="stored"`), so the feature functions during the
> backfill period — only *newly persisted grounded* findings get true relocation
> until the table refills.

## Testing (before merge — project rule: all new features have tests)

`tests/test_sarif.py`:

- **Relocation** — exact match at stored line; drifted match (excerpt moved down
  N lines); multiple matches → nearest-to-stored chosen; not-found → `stale`;
  empty excerpt → `stored`; whitespace-only reindent → still matches (not stale).
- **`build_sarif`** — severity→level mapping across all five cases; `rules`
  deduped by category; `partialFingerprints` identical when only line numbers
  differ (drift stability) and different when excerpt differs; relative
  `artifactLocation.uri`; `properties.corroborated` set from `corroborated_ids`;
  `region.snippet.text` present/omitted correctly.
- **`generate_sarif_file`** (tmp_path + real `ViolationDB`) — persist findings,
  run, assert `findings.sarif` exists and parses as JSON; stale finding excluded
  from `results` but counted in summary; deleted file → its findings stale;
  summary counts correct.

`tests/test_cli.py` (or existing CLI test module):

- `surface` happy path — DB with findings → file written, summary printed.
- No DB → friendly message, clean exit.

`tests/test_watcher.py` (or existing watcher tests):

- After `process_change` persists findings, `findings.sarif` exists.
- SARIF generation failure (monkeypatched to raise) does **not** break review
  save — `save_review` still runs, no exception propagates.

## Decisions

- **SARIF over LSP / inline comments.** SARIF is the industry-standard
  static-analysis interchange format: one file unlocks both the local editor
  (SARIF Viewer → Problems panel) and CI (GitHub code scanning), with no daemon
  and no source mutation. It fits the existing "write artifacts to
  `.ollama_reviews/`" model. LSP gives live squiggles but costs a persistent
  language-server process + editor wiring + protocol plumbing — too heavy for a
  local single-dev tool. Inline `# sentinel:` comments mutate watched code,
  re-trigger `awatch`, and pollute diffs/blame — they fight the read-only design.
- **Read-only `surface`.** Stale findings are reported, not auto-resolved.
  Mutating DB state in a surfacing command violates single responsibility and
  surprises the user; auto-resolve is a clean triage-slice follow-up.
- **Relocate by excerpt, not stored line.** Findings are anchored at review
  time; line numbers go stale. Excerpt relocation keeps the SARIF accurate and
  yields a free staleness signal (excerpt gone → stale) for the future triage
  slice.
- **`corroborated` enrichment included.** One extra `get_findings_with_incidents`
  query marks findings that already have an Incident, letting the editor/CI
  distinguish model-opinion from corroborated event. Cheap, high signal.
- **Both emit triggers.** On-demand `surface` command + best-effort watcher
  auto-refresh — a live Problems panel without an LSP daemon, plus a manual
  command for one-off / CI use.

## Risk

Lowest of the three actionability slices. No source writes, no DB mutation, no
new long-running process. New surface area: one module (`sarif.py`), one CLI
command, ~5 lines in the watcher. The only real subtlety — line drift — is
handled by excerpt relocation. The one hard prerequisite is confirmed: the
`verbatim_excerpt` column does not exist and the excerpt is discarded at persist
time, so the plan's first piece is the column + migration + `persist_findings`
write (see "Data shapes"). Everything else degrades gracefully around it.
