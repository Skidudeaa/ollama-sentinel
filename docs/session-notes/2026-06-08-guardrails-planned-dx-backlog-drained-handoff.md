# Handoff — DX Backlog Drained + Guardrails (Pattern Promotion) Planned

Date: 2026-06-08
Repo: `ollama-sentinel`
Topic: session wrap — small-DX backlog shipped; v0.3 Pattern-promotion specced + planned

---

## 1. What this session did

**Shipped + merged to master (4 PRs):**

- **PR #32 — OP-1: SIGHUP hot-reload.** `kill -HUP <pid>` reloads model /
  `request_timeout` in place (rebuilds `OllamaClient`, recomputes token budget);
  watcher keeps running; `watch.directory` changes warned-and-skipped; broken YAML
  leaves the running config intact. 6 tests incl. a real SIGHUP integration test.
  (`ollama_sentinel/watcher.py`, `processor.py`, `tests/test_hot_reload.py`.)
- **PR #33 — CB-1 closed as already-done.** The impact-report formatters were
  already deduped (canonical in `recipes.py:format_impact_report`, `synthesis.py`
  delegates). Fixed stale tracker drift instead of inventing work.
- **PR #34 — impact_scan integration test.** Drives the real `impact_scan` node
  inside a graph compiled by `build_workflow` (LLM faked, temp repo), replacing the
  mirror-logic tests. Gated by `importorskip` on the full `[research]` stack — runs
  under `pip install -e ".[research]"`, skips otherwise.
- **PR #31 — grounding P1–P4 positive tests** (salvaged from an abandoned branch
  before deleting it).

Also: refreshed `docs/VISION.md` (structural rewrite) + a prior handoff (PR #30,
earlier in the day), and pruned all merged local + remote branches.

**Net:** the small-DX backlog is fully drained — OP-1 shipped, CB-1 was already
done, the impact_scan testing gotcha is closed.

**Planned (not yet built):** the v0.3 **Pattern promotion → project guardrails**
feature — taken brainstorm → plan end to end (see §3).

---

## 2. Current state

- **master** is clean and synced with origin (tip after PRs land).
- **Test suite:** `pytest tests/ -q` → **699 passed / 16 skipped** (the +1 skip vs
  prior sessions is the gated impact_scan integration test in this partial-install
  env). Quote the command, not the count.
- The "make findings actionable" arc and v0.2 incident schema remain complete; this
  session added no code to the sentinel review path beyond OP-1.

---

## 3. Next pickup (highest leverage): build Pattern-promotion guardrails

The v0.3 north-star rung is **fully designed and planned**. Two durable artifacts:

- **Requirements:** `docs/brainstorms/2026-06-08-pattern-promotion-guardrails-requirements.md`
- **Plan:** `docs/plans/2026-06-08-001-feat-pattern-promotion-guardrails-plan.md`

**What it is:** a curated **guardrail** layer — named, LLM-checked, relevance-injected
rules. Two creation paths converge on one active-guardrail artifact:
- **Manual authoring (Phase 1, primary)** — value on day one, no incidents needed.
- **Auto-promotion (Phase 2, compounding)** — ≥3 *distinct corroborated* findings of
  the same semantic shape → a candidate the developer confirms.

**Key decisions locked (in the plan's KTDs):**
- LLM-evaluated matching, not a deterministic linter; curation gates all enforcement
  (no nagware).
- Reuse `build_review_context` injection + `SemanticRetriever`/`qwen3-embedding:4b`
  for clustering and injection ranking — no new infra.
- Auto-promotion clustering runs **on-demand**, off the review hot path.
- Candidates arrive with an **LLM-drafted assertion** the dev edits.
- Injection relevance = scope (category/path) filter → embedding rank → token budget.
- **Evidence-integrity gate:** a guardrail's own flagged findings reinforce it only
  via hard signals (`test_failure`/`fix_commit`) — prevents the Pattern-tier echo.

**To resume:** `/ce-work docs/plans/2026-06-08-001-feat-pattern-promotion-guardrails-plan.md`.
The cleanest entry is **Phase 1 (U1–U5)** — independently shippable, delivers the
whole manual-guardrail loop:
- U1 storage model + finding provenance
- U2 authoring/lifecycle CLI verbs
- U3 relevance-scoped injection
- U4 provenance capture on flagged findings
- U5 dashboard guardrails panel

Phase 2 (U6 clustering, U7 candidate curation, U8 integrity gate) layers on after.

---

## 4. Deferred / open (from the plan)

- **Guardrail staleness auto-pruning** (analogous to `prune` for findings) — deferred.
- SARIF surfacing of guardrail-flagged findings — deferred follow-up.
- Implementation-time tunables: clustering similarity threshold (U6), provenance
  attribution heuristic precision (U4), candidate-dismiss suppression duration (U7).
- **v0.3 shared substrate** (lift `ImportResolver`, unify `Finding`/`ImpactItem`,
  bidirectional flow) remains the *other* v0.3 track — bigger, can follow guardrails.

---

## 5. Persistent gotchas (not session-specific)

- Research agent needs `pip install -e ".[research]"`; this env has a **partial**
  install (missing `langchain_huggingface`), so the impact_scan integration test
  **skips** here — it runs for real only under full extras.
- `ollama-sentinel run` needs `ollama pull qwen3-embedding:4b` once for semantic
  recall (the guardrails feature depends on this embedder too).
- Qwen3 Phases B/C stay parked; `consolidation`/`rerank` roles pre-registered but
  unwired — don't pull those models speculatively.
- `docs/index.html` (pinned visual guide) predates the actionability arc and now
  lags `VISION.md`; refreshing it is a separate follow-up.
- Doc changes this session all went via `docs/`-prefixed branch + PR + rebase-merge;
  GitHub rebase-merge rewrites the prior tip's SHA, so after merging, sync local
  master with `git fetch && git reset --hard origin/master` rather than `--ff-only`.
