# Dashboard triage-console visual overhaul — design

**Status:** approved, ready for implementation plan
**Scope:** in-place visual overhaul of the Rich TUI v2 (Control Center)
render path. **No data-layer changes, no input-model changes.**
**Branch:** `feat/dashboard-triage-revamp` (off `master`, independent of
the open grounding PR #12).

---

## The problem in one paragraph

`ollama-sentinel dashboard` renders a Rich TUI that wastes most of the
screen: the Overview box is a cramped 8-line panel with a large void
beneath it, Recent Reviews is mostly low-signal `7d ago … .md` rows, and
the Patterns panel is a wrapping wall of text where the one number that
matters for action (74 CRITICAL findings) does not visually stand out.
The underlying data is rich — 1230 open findings, per-severity counts,
hottest file, recurrence — so this is purely a presentation failure. The
revamp turns the dashboard into a **triage console**: glance at it and
immediately know what to fix next.

## Locked decisions (from brainstorming)

1. **Surface & ambition:** in-place visual overhaul of the existing Rich
   TUI. Same data sources, same keyboard model (`q · Tab · j/k · Enter ·
   /`). Legacy two-panel render path (`config_path == ""`) untouched.
2. **Primary job:** triage console — severity-led; "what do I fix next"
   dominates. Liveness/status compressed to a thin strip.
3. **Layout skeleton:** vitals strip → bold severity banner + hottest
   callout → body = dominant Patterns triage list (left, wide) + narrow
   Recent-Reviews rail (right) → footer keys.
4. **Patterns ranking:** blended score `severity_weight × count`,
   weights **CRIT=8 HIGH=4 MED=2 LOW=1**, descending. One ranked list
   (not severity bands). Ties: count desc, then file path asc.
5. **Wall-of-text fix:** one line per Patterns row, ellipsis-truncated;
   full text via the existing Enter → DETAIL mode (unchanged).
6. **Palette:** bold/saturated severity-driven colors (CRITICAL bright
   red, HIGH orange/yellow, MED cyan, LOW dim), centralized so banner,
   rows, and detail agree.
7. **Recent Reviews:** demoted to a thin right rail, not dropped.

## Out of scope

- Any change to `_fetch_data`, `ViolationDB`, or new SQL queries.
- Any change to `dashboard_input.py` (keys, `UIState`, `Mode`,
  `PanelId`, `apply_key`, `key_reader_loop`).
- The legacy two-panel layout (`render_layout` when `config_path == ""`)
  — must remain byte-for-byte behaviorally identical (regression-locked).
- Detail-mode content/structure beyond palette consistency.
- A web surface, new dependencies, or restructuring the 863-line
  `dashboard.py` into modules (targeted helper extraction only).
- Config hot-reload / OP-1 (unrelated, tracked in followups.md).

---

## Architecture & components

All changes live in the **v2 render path of `ollama_sentinel/dashboard.py`**
(`render_layout` / `_build_layout` when `config_path` is set). The
`run_dashboard` loop, data fetching, error isolation, and DB-connection
reuse are unchanged.

| Function | Status | Role |
|---|---|---|
| `_SEVERITY_WEIGHT` | **new** module const | `{"critical":8,"high":4,"medium":2,"low":1}` (case-insensitive lookup, unknown → 0). |
| `_SEVERITY_STYLE` | **revised** module const | Bold/saturated Rich styles per severity; single source for banner + rows + detail. |
| `blended_rank(rows)` | **new** pure fn | `List[ViolationRow] -> List[ViolationRow]` sorted by `weight(sev)×count` desc, tie → count desc → `file_path` asc. Stable; tolerates unknown severity (weight 0, sorts last). |
| `_vitals_strip(stats, now)` | **new** (replaces `_header_panel_v2` in v2 tree) | One line: status dot (color by staleness) · model · `rev HH:MM:SS` · age. |
| `_severity_banner(stats)` | **new** | Line 1: `CRIT 74  HIGH 104  MED 576  LOW 476`, each cell styled by `_SEVERITY_STYLE`, CRITICAL most prominent. Line 2: `🔥 {hottest_file} ({n}) ▸ {suggested_action}`. Empty/no-DB → muted "no findings yet". |
| `_patterns_panel_interactive` | **revised** | Consumes pre-ranked rows; renders **one line per row**: `{count}x · {SEV} · {cat} · {file}:{line} — {desc}` with `overflow="ellipsis"`, `no_wrap=True`. Selection/scroll behavior unchanged (operates on the passed list). |
| `_reviews_rail(rows, now)` | **new** (replaces `_reviews_panel_interactive` call in v2 tree) | Compact narrow rail: `{ago}  {elided rel_path}`, basename-biased elision. Selection highlight preserved (REVIEWS panel still focusable). |
| `render_layout` / `_build_layout` v2 branch | **revised** | New region tree (below). Legacy branch unchanged. |

`compute_overview`/`OverviewStats` is reused as-is (already exposes
severity counts, hottest, model, status age, last-review age). If a
needed value is already on `stats`, no recomputation is added.

## Layout (region tree, v2 path only)

```
Layout (split_column):
  header  size 3   → _vitals_strip(stats, now)
  banner  size 4   → _severity_banner(stats)
  body    ratio 1  → split_row:
                       left  ratio 3 → _patterns_panel_interactive(ranked, sel, scroll, filt)
                       right ratio 1 → _reviews_rail(reviews, now, sel, scroll)
  footer  size 3   → _footer_interactive(state)        # non-interactive → _footer_panel_v2
```

(Footer stays `size 3`: `_footer_interactive` returns a bordered Rich
`Panel`, which needs 3 rows — top border, single content line, bottom
border. "Thin" is achieved by single-line content + `padding=(0,1)`, as
the existing footer already does, not by shrinking the region.)

- DETAIL mode still replaces `body` full-width (unchanged code path),
  using `_SEVERITY_STYLE` for consistency.
- Focus styling: `Tab` still cycles `OVERVIEW → REVIEWS → PATTERNS`
  (`_PANEL_CYCLE` unchanged). `OVERVIEW` focus highlights the **banner**
  region border (selection is a no-op there, exactly as the Overview
  panel is today). `REVIEWS` → rail border; `PATTERNS` → patterns border.

## Data flow

`_fetch_data` is unchanged. In `_build_layout` v2 branch, the existing
filter is applied to `violations` first (unchanged), then
`blended_rank()` reorders the filtered list, then the ranked list is
passed to `_patterns_panel_interactive`. Because selection/scroll indices
already index into the passed list and `_reclamp_selection` runs after
each data refresh, reordering stays consistent with no input-model
change. Filter (`/`) still substring-matches severity/category over the
(now ranked) list.

## Error handling

No change. Per-panel try/except degrade, single `ViolationDB` connection
reuse with reset-on-failure, and `asyncio.to_thread` offloading already
in `run_dashboard` are untouched. New pure helpers must not raise on
empty/missing inputs (return empty/placeholder panels) so the existing
degrade contract holds.

## Testing approach

TDD, matches the project's "tests before merge" convention. The Rich
panel/table introspection mechanism already used in
`tests/test_dashboard.py` (450 lines) is the confirmed mechanism reused
here.

- **Pure unit tests (new):**
  - `blended_rank`: severity dominates within reachable counts; count
    tiebreak; file-path tiebreak; unknown/missing severity → weight 0
    sorts last; empty input → `[]`; stability.
  - `_SEVERITY_WEIGHT` ordering invariant: `CRIT > HIGH > MED > LOW` and
    `1×CRIT > 7×LOW` (geometric-gap regression lock).
  - `_severity_banner`: contains each count and the hottest/action line;
    no-DB/empty → muted placeholder, no crash.
  - `_vitals_strip`: status dot reflects staleness; renders with empty
    model/age without raising.
- **Structural tests (new):** v2 `render_layout` yields regions
  `header / banner / body{left,right} / footer`; left:right ratio 3:1;
  Patterns rows are single-line (no wrap) given a long description.
- **Regression lock:** legacy path (`config_path == ""`) layout
  unchanged — assert existing two-panel structure still holds.
- **Existing tests:** assertions tied to the old v2 titles/structure
  (`_header_panel_v2`, `_overview_panel`, old Patterns wrapping) are
  updated to the new structure. This is a transparent, expected
  consequence of a visual overhaul and will be called out in the plan
  and PR. Legacy-path tests stay green untouched.

## Open judgment calls (resolved)

- Blended weights `8/4/2/1` (geometric so one CRIT outranks 7×LOW) —
  **accepted** by user.
- Keep Recent Reviews as a thin rail rather than cut — **accepted**.
