---
title: "docs: Refresh VISION.md (structural rewrite) + new session handoff"
type: docs
status: completed
date: 2026-06-07
depth: standard
---

# docs: Refresh VISION.md (structural rewrite) + new session handoff

## Summary

`docs/VISION.md` is the project's strategic narrative, and it has drifted. Its
"State as of this session" snapshot still reports **v0.1.0+ / 353 tests / ~2.4s
suite** (reality is ~689 passed / 15 skipped, ~10s), and it contains **no
mention of the "make findings actionable" arc** (surface → triage → remediate →
prune) that fully shipped and merged on 2026-06-04/05 — the single largest body
of work since the snapshot was written. This plan delivers a **full structural
rewrite** of `docs/VISION.md` re-sequenced around current reality, plus a new
dated **session handoff** in `docs/session-notes/`, landed via a `docs/` branch
and PR.

The rewrite re-sequences; it does **not** discard. The v0.3 shared-substrate
narrative, the north star, the explicit non-goals, and the doc's distinctive
voice are load-bearing and must survive intact.

---

## Problem Frame

`docs/VISION.md` (169 lines) is linked from the project as the canonical
"why this exists / where it's going" document. Three classes of drift:

1. **Stale snapshot.** The "## State as of this session — v0.1.0+" block
   describes a moment three-plus sessions in the past (structural recall just
   wired, 353 tests). Everything in it is true-but-ancient.
2. **A missing milestone.** The findings-actionability arc (surface, triage,
   remediate `fix <id>`, stale-prune `prune`) is the project's headline
   capability now — it turns model opinions into closeable, editor-visible,
   auto-fixable, auto-prunable Findings. The vision doc never mentions it.
   Phase A (Qwen3 hot-path embedding swap), the dashboard, and the `incidents`
   CLI are also absent or under-stated.
3. **Sequencing rot.** Because new milestones were never folded in, the doc's
   "next state" (v0.3) sits directly after a v0.1-era snapshot, skipping the
   reality that v0.2 *and* a whole actionability arc shipped in between.

The fix is not a patch — the user has chosen a structural rewrite so the doc
reads as a coherent current-state narrative again. The companion deliverable is
a session handoff capturing this session's state and the next pickable moves,
following the `docs/session-notes/` convention.

---

## Scope Boundaries

**In scope**
- Full structural rewrite of `docs/VISION.md` around current state.
- A new dated handoff doc in `docs/session-notes/`.
- A consistency pass on inbound references to VISION.md (README, `docs/index.html`,
  `docs/GUIDE.md`, CLAUDE.md) — verify links/claims still resolve after the rewrite.
- Landing on a `docs/<date>-...` branch with a PR.

### Deferred to Follow-Up Work
- Rewriting `docs/index.html` (the canonical visual guide). It is pinned as
  do-not-move and is a separate, larger surface; only verify it doesn't
  contradict the refreshed VISION.md, don't rewrite it here.
- Refreshing `README.md` prose beyond reference-integrity checks.
- Editing the CLAUDE.md "Known Issues / Next Session Breadcrumbs" section as the
  primary handoff (the user chose a standalone `docs/session-notes/` doc; a
  one-line CLAUDE.md pointer is optional and lives in U4, not a rewrite).

### Non-goals
- **No code changes.** Zero edits under `ollama_sentinel/`, `research_agent/`,
  or `tests/`. The test suite must remain green and unchanged.
- **No discarding of forward narrative.** The v0.3 substrate plan, north star,
  non-goals, and "what this is not" content are preserved (re-sequenced/tightened
  at most, not deleted).
- **No new product claims.** The rewrite reports what shipped; it does not
  promise capability that does not exist (the doc itself warns against
  "truth-in-advertising" gaps — honor that).

---

## Key Decisions

| # | Decision | Rationale |
|---|----------|-----------|
| D1 | **Full structural rewrite** of VISION.md (not a patch) | User choice. The doc reads incoherently as a stale snapshot + future-only tail; re-sequencing around current state restores the narrative. |
| D2 | **Preserve the forward narrative** (v0.3 substrate, north star, non-goals, "what this is not", voice) | These are the doc's durable strategic core and the reason the user values it. A rewrite re-sequences; it must not gut. Explicit guard against the known failure mode of "rewrite" = "start over." |
| D3 | Handoff = **new standalone doc in `docs/session-notes/`** | User choice. Matches the existing `docs/session-notes/2026-05-05-...` convention; durable and narrative, separate from CLAUDE.md. |
| D4 | Land via **`docs/<date>-...` branch + PR** | User choice. Matches the repo's recent doc-change convention (`docs/handoff-2026-06-05`, `docs/2026-05-16-session-handoff` branches). |
| D5 | **Never hardcode the test count** | CLAUDE.md is explicit: "Do not hardcode the number here again — it drifts. Quote the command, not the count." The rewrite reports the suite via the command (`pytest tests/ -q`) and uses the live count captured at write time, framed as "as of <date>". |
| D6 | CLAUDE.md breadcrumbs are **already current** — don't duplicate | The breadcrumbs section already reflects arc-complete / 689-15. The new handoff doc complements it (narrative session record), it doesn't replace or re-state it; an optional one-line pointer is the only CLAUDE.md touch. |

---

## Current-State Fact Base

The source of truth the rewrite must reflect (verify each in U1 before writing):

- **v0.1.0+** — public on GitHub, structural recall (ImportResolver 1-hop
  import-graph neighbors) wired into the sentinel hot path. *(already in doc)*
- **v0.2 — Finding/Incident split — SHIPPED.** Incident schema + migration,
  opt-in pytest plugin, `confirm` verb, post-commit hook (`install-hooks` /
  `record-commit`), `incidents` verb. *(already in doc, current)*
- **Phase A — Qwen3 hot-path embedding swap — SHIPPED (2026-05-01).** Hot-path
  embedder nomic-embed-text → qwen3-embedding:4b; named-role EmbeddingConfig;
  consolidation/rerank roles pre-registered but unwired (Phases B/C parked).
- **"Make findings actionable" arc — COMPLETE (merged 2026-06-04/05).** The
  headline addition:
  - `surface` — emit open Findings to `.ollama_reviews/findings.sarif` (editor
    Problems panel + CI). (PR #14)
  - `triage` — pipe tool output (pytest/mypy/ruff/traceback) → local-model
    diagnosis with auto-extracted source context. (PR #15)
  - `fix <id>` — localized, excerpt-verified, whole-line-span fix: preview diff,
    write on confirm, resolve as fixed; atomic UTF-8/CRLF-preserving write,
    never commits. (PRs #22–26)
  - `prune` — close findings whose flagged code is gone (preview + confirm,
    `resolution='stale'`, no Incident). (PR #29)
  - Supporting verbs: `findings`, `resolve`, `dismiss`, `dashboard` (live Rich TUI).
- **v0.3 — shared substrate — STILL ASPIRATIONAL.** Lift `ImportResolver` to
  shared infra, unify `Finding`/`ImpactItem`, bidirectional sentinel↔research
  flow. *(preserve verbatim in intent)*
- **Test reality** — run `pytest tests/ -q`; quote the command and the
  date-stamped live count (last documented: 689 passed / 15 skipped, ~10s — do
  not trust this number, re-measure).

---

## High-Level Design — Target VISION.md Structure

The rewrite maps the existing material onto a current-state-first sequence.
This is directional guidance for the section order, not prescriptive prose.

```
BEFORE (current)                          AFTER (target)
─────────────────────────                 ─────────────────────────────
# Vision (intro)                          # Vision (intro)            ← keep, tighten
## What it actually is                     ## What it actually is      ← keep, current
## State as of this session — v0.1.0+      ## Where it is now (v0.2 +  ← REPLACE stale snapshot
   (353 tests, structural recall)             actionability arc)         with a current milestone-
## What's still aspirational                                              accurate state section
## v0.2 — Finding/Incident split (shipped) ## How memory accrues       ← v0.2 split, kept current
## Next state — v0.3: shared substrate     ## Making findings          ← NEW: surface→triage→
## Explicit non-goals                         actionable (shipped)        remediate→prune arc
## North star                              ## What's still aspirational ← v0.3 substrate (PRESERVE)
## What this is not                        ## Explicit non-goals       ← PRESERVE
                                           ## North star               ← PRESERVE
                                           ## What this is not          ← PRESERVE
```

Net change: the v0.1-era snapshot is replaced by a current-state section; a new
"making findings actionable" milestone section is inserted; the forward/strategic
tail (aspirational, non-goals, north star, what-this-is-not) is preserved.
Section titles above are directional — the implementer may adjust naming to fit
the doc's voice.

---

## Implementation Units

### U1. Establish and verify the current-state fact base

**Goal:** Pin every factual claim the rewrite will make, so the new doc kills
staleness instead of introducing it.
**Dependencies:** none.
**Files:** none created (read-only verification pass). Inputs: `docs/VISION.md`,
`CLAUDE.md` (Recent landings + breadcrumbs), `ollama_sentinel/cli.py` (verb
surface), `README.md`.
**Approach:**
- Run `pytest tests/ -q`; capture the live pass/skip count and date-stamp it.
- Confirm the shipped verb surface against `ollama_sentinel/cli.py` (surface,
  triage, fix, prune, findings, resolve, dismiss, incidents, confirm, dashboard,
  install-hooks, record-commit) — the rewrite must not name a verb that isn't wired.
- Confirm PR-merge facts for the actionability arc and Phase A against CLAUDE.md
  "Recent landings".
- Produce a short internal checklist (the Current-State Fact Base section above,
  each item ✓-verified).
**Execution note:** This is a guard against the rewrite re-introducing drift.
Do it before writing prose.
**Test scenarios:** Test expectation: none — read-only verification, no artifact
and no behavioral change. Verification is the completed fact checklist.
**Verification:** Every bullet in "Current-State Fact Base" is confirmed against
a primary source (code or merged-PR record), and the live test count is captured
with its command.

### U2. Structurally rewrite `docs/VISION.md`

**Goal:** Re-sequence the vision doc around current state per the target
structure, preserving the forward narrative and voice.
**Dependencies:** U1.
**Files:** `docs/VISION.md` (rewrite).
**Approach:**
- Follow the BEFORE→AFTER map in High-Level Design.
- Replace the "State as of this session — v0.1.0+" block with a current-state
  section (v0.2 shipped, actionability arc shipped, Phase A shipped), date-stamped.
- Insert a "making findings actionable (shipped)" section covering
  surface→triage→remediate→prune, framed as the bridge between *model opinion*
  (Finding) and *acted-on outcome* (resolved/fixed/pruned/corroborated).
- Carry the v0.2 Finding/Incident section forward (it's current — tighten only).
- **Preserve (D2):** the v0.3 substrate section, explicit non-goals, north star,
  and "what this is not" — re-sequenced after current-state, not deleted or
  watered down.
- Keep the doc's voice (declarative, "the memory is the product", anti-hype).
- Apply D5: report the suite via command + date-stamped live count, never a bare
  hardcoded number presented as evergreen.
**Patterns to follow:** the existing VISION.md voice and the v0.2 section's
"what shipped" bullet style; the truth-in-advertising discipline the doc itself
preaches (no claim the code doesn't back).
**Test scenarios:** Test expectation: none — documentation, no behavioral change.
Content correctness is verified by the U1 fact checklist and the U2 verification
list below.
**Verification:**
- The stale v0.1.0+ snapshot is gone; a current-state section replaces it.
- The actionability arc has a dedicated section naming all four verbs accurately.
- The v0.3 substrate narrative, north star, non-goals, and "what this is not"
  are all still present and substantively intact (diff-confirm none were dropped).
- No verb or capability is named that isn't wired in `cli.py` (cross-check U1).
- No hardcoded evergreen test count; suite is reported per D5.

### U3. Author the session handoff doc

**Goal:** A durable, narrative session handoff in `docs/session-notes/`.
**Dependencies:** U1 (facts), U2 (so the handoff can reference the refreshed doc).
**Files:** create `docs/session-notes/2026-06-07-vision-refresh-and-arc-complete-handoff.md`.
**Approach:** Mirror the structure of `docs/session-notes/2026-05-05-ux-centralization-session-summary.md`.
Capture: what this session did (VISION.md rewrite + this handoff), the
arc-complete current state (v0.2 + actionability arc + Phase A), the live test
count with command, and the next pickable moves (OP-1 SIGHUP hot-reload, CB-1
formatter dedupe — from CLAUDE.md "Pickable next moves"), plus the persistent
gotchas worth carrying (qwen3-embedding pull requirement, unwired
consolidation/rerank roles, `_archive/` no-import rule).
**Patterns to follow:** existing `docs/session-notes/` and `docs/retros/` doc
shape; CLAUDE.md breadcrumbs tone (concrete, dated, next-move-oriented).
**Test scenarios:** Test expectation: none — documentation. Verified by the
checklist below.
**Verification:** The handoff names the current state accurately, links to the
refreshed `docs/VISION.md`, lists concrete next moves with effort/risk, and
follows the session-notes naming convention.

### U4. Reference-integrity + consistency pass

**Goal:** Ensure the rewrite didn't break inbound references or contradict
sibling docs.
**Dependencies:** U2, U3.
**Files:** read `README.md`, `docs/GUIDE.md`, `docs/index.html`, `CLAUDE.md`;
optionally a one-line pointer edit in `CLAUDE.md` and/or `README.md` if they
link to VISION.md sections that were renamed.
**Approach:**
- Grep for references to VISION.md and to any renamed section anchors; fix
  broken anchors/links only (no prose rewrites — those are deferred).
- Confirm `docs/index.html` (pinned, do-not-move) does not now contradict the
  refreshed VISION.md; if it does, note it as a follow-up rather than editing it
  here (out of scope).
- Optionally add a one-line pointer from CLAUDE.md breadcrumbs to the new
  session-notes handoff (D6) — additive, not a rewrite.
**Test scenarios:** Test expectation: none — documentation. Plus a guard:
`pytest tests/ -q` still green and unchanged (proves U1–U4 touched no code).
**Verification:** No broken links/anchors to VISION.md; `docs/index.html`
either consistent or its drift logged as a follow-up; test suite green and
unchanged from pre-plan baseline.

---

## Risks & Mitigations

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| "Full rewrite" guts the forward strategic narrative (the part the user values) | Med | D2 + U2 verification explicitly diff-confirms v0.3 / north star / non-goals / "what this is not" survive. Treat preservation as a hard acceptance gate. |
| Rewrite re-introduces stale or false claims (wrong verb names, fabricated capability) | Med | U1 verifies every claim against `cli.py` and merged-PR record before any prose is written. |
| Hardcoded test count drifts again | High (historically) | D5: report via command + date-stamped live count; never an evergreen bare number. |
| Renamed section anchors break inbound links | Low | U4 grep + fix pass. |
| Editing `docs/index.html` by accident (it's pinned do-not-move) | Low | Explicit non-goal + scope boundary; U4 only *reads* it. |

---

## Open Questions / Deferred

- **`docs/index.html` consistency.** If the visual guide materially contradicts
  the refreshed VISION.md (e.g. still implies the actionability arc is future
  work), refreshing it is a separate, larger task — log as follow-up, don't
  pull into this PR.
- **README prose refresh.** Same treatment — reference-integrity only here; a
  README narrative refresh is deferred.

---

## Sources & Research

- `docs/VISION.md` (current, 169 lines) — the rewrite target.
- `CLAUDE.md` — "Recent landings" and "Known Issues / Next Session Breadcrumbs"
  (the authoritative shipped-state record; already arc-complete-current).
- `ollama_sentinel/cli.py` — the wired verb surface (ground truth for capability claims).
- `docs/session-notes/2026-05-05-ux-centralization-session-summary.md` — handoff doc shape.
- Git: branches `docs/handoff-2026-06-05` (CLAUDE.md-breadcrumbs style) and
  `docs/2026-05-16-session-handoff` (standalone-retro style) — the two existing
  handoff conventions; this plan follows the standalone-doc style per D3.
