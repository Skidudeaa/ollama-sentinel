# Decision Record Index

Last updated: 2026-08-25 (accuracy pass; project parked)

> **Only ADR-0001 was ever written.** Numbers 0002–0008 were reserved in this
> index on 2026-05-02 and the records themselves never landed — the tables
> below described files that do not exist, and every link to them was broken.
> Rather than fabricate the content retroactively, they are now marked
> `unwritten` with a pointer to where each decision is actually recorded.
> The v0.3 rows the old index promised ("ADRs will be written as v0.2 Incident
> work ships") never materialised either; the project was parked 2026-08-25.

---

## Tag vocabulary

Tags are a closed set. To add a new tag, add a `### tag_name` heading
under "By tag" below and document what it covers in one line.

| Tag | Covers |
|---|---|
| `schema` | Data models, SQLite DDL, Pydantic config shapes, migration patterns |
| `recall` | Semantic, structural, and single-file recall strategies |
| `embedding` | Embedding models, config, OllamaEmbedder, cache keys |
| `hooks` | Git hooks, commit linkage, pytest plugin |
| `config` | YAML shape, validation, legacy migration, defaults |
| `testing` | Test patterns, conventions, coverage strategies |
| `process` | Spec/plan/review workflow, deviation tracking, documentation |
| `architecture` | Module boundaries, shared infra, cross-module concerns |

---

## By number

| # | Title | Status | Date | Tags |
|---|---|---|---|---|
| 0001 | Three-layer recall cascade | accepted | 2026-05-01 | recall, architecture |
| 0002 | Pre-registered embedding roles with merge-in-validator | **unwritten** | 2026-05-02 | embedding, config |
| 0003 | Closed-set role name enforcement | **unwritten** | 2026-05-02 | embedding, config |
| 0004 | Closure-grep guard test pattern | **unwritten** | 2026-05-01 | testing |
| 0005 | Leaf-module prompt extraction for testability | **unwritten** | 2026-05-01 | testing, architecture |
| 0006 | Spec deviation tracking in PR descriptions | **unwritten** | 2026-05-02 | process |
| 0007 | Schema-complete pre-registration over minimal-then-migrate | **unwritten** | 2026-05-01 | config, process |
| 0008 | Park Phases B/C until Incident schema lands | **unwritten** | 2026-05-01 | process, embedding |

---

## By tag

### schema

(No entries. ADR-0009+ never landed — see the note at the top.)

### recall

- [0001 — Three-layer recall cascade](0001-three-layer-recall.md)

### embedding

- 0002 — Pre-registered embedding roles with merge-in-validator — *unwritten; recorded in Phase A spec: `docs/superpowers/plans/2026-05-01-phase-a-qwen3-hot-path-swap.md`*
- 0003 — Closed-set role name enforcement — *unwritten; recorded in same Phase A spec; enforced by `EmbeddingConfig` (`ollama_sentinel/models.py`)*
- 0008 — Park Phases B/C until Incident schema lands — *unwritten; recorded in CLAUDE.md “persistent gotchas”*

### hooks

(No entries. ADR-0009+ never landed — see the note at the top.)

### config

- 0002 — Pre-registered embedding roles with merge-in-validator — *unwritten; recorded in Phase A spec: `docs/superpowers/plans/2026-05-01-phase-a-qwen3-hot-path-swap.md`*
- 0003 — Closed-set role name enforcement — *unwritten; recorded in same Phase A spec; enforced by `EmbeddingConfig` (`ollama_sentinel/models.py`)*
- 0007 — Schema-complete pre-registration over minimal-then-migrate — *unwritten; recorded in Phase A spec*

### testing

- 0004 — Closure-grep guard test pattern — *unwritten; recorded in CLAUDE.md landings + `tests/test_research_agent.py`*
- 0005 — Leaf-module prompt extraction for testability — *unwritten; recorded in `research_agent/core/prompts.py`, TR-1 in `docs/superpowers/followups.md`*

### process

- 0006 — Spec deviation tracking in PR descriptions — *unwritten; recorded in `docs/superpowers/followups.md` (TR-3)*
- 0007 — Schema-complete pre-registration over minimal-then-migrate — *unwritten; recorded in Phase A spec*
- 0008 — Park Phases B/C until Incident schema lands — *unwritten; recorded in CLAUDE.md “persistent gotchas”*

### architecture

- [0001 — Three-layer recall cascade](0001-three-layer-recall.md)
- 0005 — Leaf-module prompt extraction for testability — *unwritten; recorded in `research_agent/core/prompts.py`, TR-1 in `docs/superpowers/followups.md`*

---

## By version

### v0.1.0 (2026-04-30)

(Pre-ADR system. Decisions from this era can be backfilled from
CLAUDE.md "Recent landings" and commit messages if they're still
load-bearing.)

### v0.2.0 (2026-05-02)

- 0001 — Three-layer recall cascade
- 0002 — Pre-registered embedding roles with merge-in-validator
- 0003 — Closed-set role name enforcement
- 0004 — Closure-grep guard test pattern
- 0005 — Leaf-module prompt extraction for testability
- 0006 — Spec deviation tracking in PR descriptions
- 0007 — Schema-complete pre-registration over minimal-then-migrate
- 0008 — Park Phases B/C until Incident schema lands

### v0.3.0 — v0.2 Incident schema + v0.3 guardrails (shipped 2026-05/06)

No ADRs were written for this era despite it being the largest architectural
arc in the project (Finding → Incident → Pattern). The decisions live in
`docs/plans/2026-06-08-001-feat-pattern-promotion-guardrails-plan.md`, the
2026-06-13 session notes, and CLAUDE.md's landings table.

### Parked (2026-08-25)

The ADR practice lapsed after 2026-05-02 and was never revived. Recording that
plainly is more useful than backfilling records nobody wrote at the time. See
`docs/session-notes/2026-08-25-bake-verdict-and-fleet-shutdown.md`.

---

## Supersession chains

(None yet. First chain will appear when a v0.2 decision supersedes
a v0.1 assumption.)
