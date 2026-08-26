# Bake harvest — the defects worth keeping (2026-08-25)

The 2026-08-08→25 dogfood bake produced **18,864 findings** across six
projects. The fleet is now shut down and nobody is going to open those SQLite
files again. This is the salvage: the findings that describe an actual defect,
pulled out before they became archaeology.

Machine-readable companion: `bake-harvest-2026-08-25.json` (368 findings,
score ≥ 4, full text preserved).

## How this list was built

The stored `severity` column is untrustworthy — `extractor.py` scans severity
keywords across the whole finding *including the quoted code*, so a CSS
`.critical` class or `{ critical_high: 'HH' }` promotes a style nit to
critical (71% of soma's "critical" tier). See CLAUDE.md next-move #1.

So severity was recomputed offline:

1. Strip fenced and inline code — score only what the model actually *asserted*.
2. Take the model's explicit `**Severity:** X` or 🔴 when present (only ~6% of
   findings have one).
3. Otherwise score on defect signals (`race condition`, `off-by-one`, `xss`,
   `injection`, `crash`, `leak`, `silently`, `corrupt`, `collision`,
   `unvalidated`, …) minus taste signals (`consider`, `magic number`,
   `readability`, `extract`, `rename`, …).
4. Collapse duplicates by normalized prose signature, summing occurrences.
5. Drop scratch dirs (`.playwright-cli/`, `.design-handoff/`, `.worktrees/`)
   and findings whose prose is *praise* rather than a complaint.

**18,864 → 368 at score ≥ 4.**

**Precision caveat — read this before acting.** The filter is heuristic and
roughly **1 in 8 of the top items is not a defect.** Two failure modes seen:
the model praising code (`test_epic_token_store.py:114` — "verifying that a key
mismatch raises rather than silently returning corrupted data is the correct
fail-closed behavior") and the model explicitly clearing something (a migration
f-string it labels "non-issue... no SQL injection risk"). The praise filter
catches most but not all. **Verify each item against the source before fixing.**
Line numbers have also drifted — findings store the line as of review time and
no `verbatim_excerpt` was captured (next-move #2), so treat locations as hints.

---

## soma — highest stakes, do these first

Production clinical app where the working tree *is* production. 249 findings
kept. The security cluster matters most:

| File:line | Issue |
|---|---|
| `core/settings.py:251` | **Hardcoded API key fallback** — `if not APP_API_KEY: APP_API_KEY = "1977"`. A credential in source control. |
| `api/middleware/csrf.py:34` | **CSRF fallback secret regenerated every restart** — `os.getenv("CSRF_SECRET_KEY", secrets.token_urlsafe(32))`. If the env var is unset, every restart silently invalidates all outstanding tokens. |
| `static/js/census/components/classification-rationale.js:78,82,111,117,142,161,166` | **XSS** — render functions interpolate API-sourced values into HTML unescaped. Model rated it HIGH itself. |
| `static/js/census/components/episode-tapestry-column.js:4371` | **XSS via attribute-selector injection** — `` querySelectorAll(`[data-lane-key="${key}"]`) `` with `key` derived from a name. |
| `ir/documents/templates/necessity_letter.md.j2:4,6,10,22` | Jinja renders `{{ payer }}`, `{{ procedure_name }}`, `{{ snippet }}`, `{{ cite }}` unescaped. |
| `scripts/adjudicate_transfer_case_value_mismatches.py:139` | **SQL injection surface** — `f"SELECT {attribute_key} FROM transfer_workspaces WHERE id = ?"`. Comment claims `attribute_key` is whitelisted; the model doubts the whitelist is enforced on that path. |
| `api/endpoints/notes.py:5877` | `delete_note` leaks internal error detail to clients — `HTTPException(500, detail=f"...{str(e)}")`. |

Correctness, ranked:

- **`hyperdrive/router.py:7363-7548` — race condition in `snapshot_approved_problems`.** Model's own rating: `Severity: HIGH`, "version conflict detection has a logic error that can cause **silent data loss**." Highest-value single finding in the whole bake.
- `services/rail_bhc_service.py:445-447` — `_daily_evidence` swallows encounter-mismatch as "degraded", conflating **data corruption with unavailability**. Seen 2×.
- `api/endpoints/progress_oneshot.py:461` — `_evidence_tokens` `rstrip("s")` silently corrupts token matching. (`rstrip` strips *all* trailing `s`, so `"assess"` → `"asse"`.)
- `static/js/census/index.js:9418` — `boot()` is async, called unawaited; a throw is silently swallowed. Seen 4×.
- `static/js/admissions/index.js:30,38,43` — `localStorage` access unguarded; throws in private browsing or when storage is full, crashing `_initListToggle`.
- `services/diff_oracle_service.py:306`, `services/prior_note_service.py:331`, `scripts/backfill_purge_junk_medications_2026_07_28.py:108`, `scripts/backfill_course_projection_2026_08_19.py:180` — bare `except Exception` swallowing corrupt-file and JSON errors. The backfill ones mean a corrupt store file is silently skipped from a backfill.

## theater6 — 43 kept

- `Theater6/Features/Timelapse/BriefingControlLayerController.swift:248,421` — **frame-ID collision**: `TerritoryFrameRef(id: Int(updatedAt.timeIntervalSince1970))` is second-resolution, so two frames in the same second collide. Seen 3×, model rated 🔴 High.
- `Theater6/Infrastructure/Persistence/NewsDataCache.swift:89-91,109-114` — `try?` swallows decode failures; "no cache" and "corrupt cache" become indistinguishable.
- `Theater6/Theater6App.swift:6120-6139` — `reloadLibrary` swallows partial failure: if `fetchAnalyses` fails but `fetchAvailableMegathreads` succeeds, the user gets a misleading state. Related: `:6548-6567`, `:6113` (`calendarStartDate` silently wrong fallback).
- `Theater6/Theater6App.swift:5986` + `:6119` — window mismatch: `fetchAvailableMegathreads(days: 90)` against a `calendarStartDate` of `-89` days. Off-by-one or deliberate inclusive endpoint — worth one look.
- `Theater6/Theater6App.swift:2400` — `@State private var backendBaseURL = "http://167.99.113.232:8001"`: hardcoded **cleartext HTTP** droplet IP in source. Not a credential, but it pins infra topology and permits interception.
- `Theater6/Theater6App.swift:4613` — force `try!` in debug code.
- `Theater6/Models/DeepStateHistoricalDataManager.swift:75-78` — silent catch in `loadAvailableDates`.

## dictum + dictumd — 34 kept

- **`scripts/deploy.sh:70` (dictumd) — top-scoring finding of the entire bake.** `sqlite3` failure is silently swallowed by command substitution: a missing binary, locked DB, or corrupt file makes the pragma return empty and the deploy proceeds as if it succeeded.
- `dictum/proxy/relay.py:253-258, 587-636` — race condition in `_flush_flux_tail`; `_flux_turn_ended` is **never cleared between sessions**. Seen 3×.
- `apple/Shared/Audio/AudioCaptureEngine.swift:169, 203` — reads `outputBuffer.int16ChannelData` without confirming `frameLength` is set; zero/invalid `outputFrameCount` is a real crash path.
- `apple/iOS/WatchRelay/WatchSessionRelay.swift:199,206` — `pending[memoId]` is only removed in `handleResult`, so a silent upload failure leaks the entry permanently.
- `apple/Watch/DirectUploadTransport.swift:126-129` — a malformed `creds.serverURL` is caught and recorded as an **empty transcript**: the user gets a memo with no text and no error.
- `apple/iOS/DictumIOSApp.swift:55, 63` — two `try!` force-unwraps in fallback/simulator init paths.
- `apple/Watch/WatchConnectivityClient.swift:84`, `WatchSessionRelay.swift:230` — encoding failures silently swallowed.

## dwell — 36 kept (all pre-bake, June)

dwell never ran during the bake; these are from June and may already be fixed.

- `Sources/thomasamosson/main/UnifiedSongInfoView.swift:1201` — `highlightClearWork` reassigned without cancelling: concurrent animations, possible leak.
- `.../HeatmapSpineDocks.swift:186` — `spine.albumYear!` force-unwrap bypasses the nil filter `dockableSpines` already applies.
- `.../welcome/NTOWelcomeView.swift:358` — `currentAppleMusicSong!.title` force-unwrapped in an accessibility hint.
- `.../UnifiedSongInfoView.swift:1041` — `threadTarget` doesn't handle a nil `artistTimelineViewModel.timeline`.
- `.../ContentZones/FixedBudgetPanel.swift:1253` — `try? await Task.sleep` swallows cancellation.
- `.../UnifiedSongInfoView.swift:268` — `currentSongYear` parses an ISO date without validating format.

## feb8 — 6 kept

- `scripts/2026-08-10-animated-artwork-media-heal.py:145, 173, 178` — `json.loads(jdoc["file_data"])` crashes when the Mongo doc is missing or `file_data` is corrupt. Flagged three times at three call sites; one is marked **STILL PRESENT** across runs.
- `scripts/2026-08-11-animated-artwork-album-backfill.py:51-54` — broad exception catch.
- `tests/test_album_animated_propagation.py:76` — missing coverage for two previously-flagged issues: a race in the MP4 existence check and regex injection via the `artist` parameter.

---

## Suggested order

1. **soma security** — the hardcoded `"1977"` key and the CSRF fallback secret are one-line each. Then the XSS cluster.
2. **soma `hyperdrive/router.py:7363`** — the silent-data-loss race. Biggest single risk found.
3. **`dictumd/scripts/deploy.sh:70`** — a deploy that reports success after a failed integrity check.
4. Everything else is real but lower-stakes; the force-unwrap and bare-except clusters are mechanical.

Nothing here is auto-fixable from the tool: the legacy extraction path never
captured `verbatim_excerpt`, so `ollama-sentinel fix` cannot bound an edit for
any of these (CLAUDE.md next-move #2). Treat this as a reading list.
