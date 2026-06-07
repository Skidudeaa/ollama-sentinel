# Vision Refresh + Arc-Complete Handoff

Date: 2026-06-07
Repo: `ollama-sentinel`
Topic: Refresh `docs/VISION.md` to current state; capture the next-session handoff

---

## 1. What this session did

- **Structurally rewrote `docs/VISION.md`.** The doc had drifted: its "State as
  of this session" snapshot still reported v0.1.0+ / 353 tests / ~2.4s, and it
  never mentioned the "make findings actionable" arc — the largest body of work
  since that snapshot. The rewrite re-sequences the doc around current reality
  while preserving the forward narrative (v0.3 shared substrate, north star,
  explicit non-goals, "what this is not").
- **Wrote this handoff** in `docs/session-notes/` (the standalone-doc handoff
  convention, alongside the CLAUDE.md breadcrumbs).
- **No code changed.** Docs-only. Suite is green and unchanged.

Plan of record: `docs/plans/2026-06-07-001-docs-refresh-vision-and-handoff-plan.md`.

---

## 2. Current state (the source of truth the rewrite reflects)

- **Public on GitHub**, <https://github.com/Skidudeaa/ollama-sentinel>.
- **Test suite:** `pytest tests/ -q` → **689 passed / 15 skipped in ~9s** as of
  2026-06-07. Quote the command, not the count — it drifts every time tests land.
- **v0.2 — Finding/Incident split: SHIPPED.** Incident schema + migration,
  opt-in pytest plugin, `confirm` verb, post-commit hook
  (`install-hooks` / `record-commit`), `incidents` verb. Findings are model
  opinions; Incidents are corroborated events.
- **Phase A — hot-path embedding swap: SHIPPED (2026-05-01).** Embedder
  `nomic-embed-text` → `qwen3-embedding:4b`; `EmbeddingConfig` is a named-role
  dict; `consolidation`/`rerank` roles pre-registered but unwired (Phases B/C
  parked).
- **"Make findings actionable" arc: COMPLETE (merged 2026-06-04/05).**
  - `surface` (PR #14) — SARIF 2.1.0 to `.ollama_reviews/findings.sarif`,
    editor Problems panel + CI, excerpt-based relocation, watcher auto-refresh.
  - `triage` (PR #15) — pipe tool output → local-model diagnosis with
    auto-extracted source context.
  - `fix <id>` (PRs #22–26) — localized, excerpt-verified, whole-line-span fix;
    preview diff, write on confirm, resolve as fixed; atomic
    UTF-8/CRLF/mode-preserving write; never commits.
  - `prune` (PR #29) — close findings whose flagged code is gone
    (`resolution='stale'`, no Incident); read-only on source.
  - Supporting verbs: `findings`, `resolve`, `dismiss` (idempotent),
    `dashboard` (live Rich TUI).
- **v0.3 — shared substrate: STILL ASPIRATIONAL.** Lift `ImportResolver` to
  shared infra, unify `Finding`/`ImpactItem`, bidirectional sentinel↔research
  flow.

---

## 3. Next pickable moves (ordered by leverage)

| # | Item | Effort | Risk | Notes |
|---|------|--------|------|-------|
| 1 | **OP-1** — SIGHUP hot-reload of `ollama-sentinel.yaml` (`docs/superpowers/followups.md`) | M | med | Real DX pain on long-running watchers. |
| 2 | **CB-1** — dedupe impact-report formatter (`recipes.py` vs `synthesis.py`) | ~30–45 min | low | Dormant; only triggers if `build_research_context` gets impact data. |

Skip **TR-3** — deliberate spec deviation, documented in `docs/superpowers/followups.md`.
Qwen3 Phases B/C stay parked (no demand; the Phase-A plan forbids pulling the
models speculatively).

The "make findings actionable" arc is complete — no arc work left.

---

## 4. Persistent gotchas (not session-specific)

- Research agent requires `pip install -e ".[research]"` (heavy deps: langchain,
  playwright, llama-index). Not installed by default.
- `ollama-sentinel run` requires `ollama pull qwen3-embedding:4b` once on first
  use (~2.5 GB), or set `memory.semantic_recall: false` to fall back to
  legacy exact-path recall.
- `embedding.models.consolidation` and `embedding.models.rerank` are
  pre-registered in the schema but **unwired**. Do not pull
  `qwen3-embedding:8b` or any reranker unless picking up Phase B or C.
- `impact_scan` node is tested with mocked logic only — needs an integration
  test against a real LangGraph compile with `OPENAI_API_KEY`.
- `_archive/` holds superseded snapshots. Do not import from it.
- `docs/index.html` is the canonical visual guide — pinned, do-not-move. It was
  **not** rewritten this session; if it now lags the refreshed VISION.md
  (e.g. implies the actionability arc is future work), refreshing it is a
  separate follow-up.

---

## 5. Where things live

- Vision: `docs/VISION.md` (refreshed this session).
- Canonical handoff breadcrumbs: the "Known Issues / Next Session Breadcrumbs"
  section in `CLAUDE.md` (already current — this doc complements it).
- Follow-ups with hashes: `docs/superpowers/followups.md`.
- Plans: `docs/plans/`. Specs: `docs/superpowers/specs/`.
