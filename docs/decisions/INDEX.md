# Decision Record Index

Last updated: 2026-05-02

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
| 0002 | Pre-registered embedding roles with merge-in-validator | accepted | 2026-05-02 | embedding, config |
| 0003 | Closed-set role name enforcement | accepted | 2026-05-02 | embedding, config |
| 0004 | Closure-grep guard test pattern | accepted | 2026-05-01 | testing |
| 0005 | Leaf-module prompt extraction for testability | accepted | 2026-05-01 | testing, architecture |
| 0006 | Spec deviation tracking in PR descriptions | accepted | 2026-05-02 | process |
| 0007 | Schema-complete pre-registration over minimal-then-migrate | accepted | 2026-05-01 | config, process |
| 0008 | Park Phases B/C until Incident schema lands | accepted | 2026-05-01 | process, embedding |

---

## By tag

### schema

(No entries yet — ADR-0009+ will land with the v0.2 Incident work.)

### recall

- [0001 — Three-layer recall cascade](0001-three-layer-recall.md)

### embedding

- [0002 — Pre-registered embedding roles with merge-in-validator](0002-pre-registered-roles.md)
- [0003 — Closed-set role name enforcement](0003-closed-set-roles.md)
- [0008 — Park Phases B/C until Incident schema lands](0008-park-phases-bc.md)

### hooks

(No entries yet — ADR-0009+ will land with the v0.2 Incident work.)

### config

- [0002 — Pre-registered embedding roles with merge-in-validator](0002-pre-registered-roles.md)
- [0003 — Closed-set role name enforcement](0003-closed-set-roles.md)
- [0007 — Schema-complete pre-registration over minimal-then-migrate](0007-schema-complete-pre-registration.md)

### testing

- [0004 — Closure-grep guard test pattern](0004-closure-grep-guards.md)
- [0005 — Leaf-module prompt extraction for testability](0005-leaf-module-prompts.md)

### process

- [0006 — Spec deviation tracking in PR descriptions](0006-spec-deviation-tracking.md)
- [0007 — Schema-complete pre-registration over minimal-then-migrate](0007-schema-complete-pre-registration.md)
- [0008 — Park Phases B/C until Incident schema lands](0008-park-phases-bc.md)

### architecture

- [0001 — Three-layer recall cascade](0001-three-layer-recall.md)
- [0005 — Leaf-module prompt extraction for testability](0005-leaf-module-prompts.md)

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

### v0.3.0 (pending — Incident schema)

(ADRs will be written as v0.2 Incident work ships.)

---

## Supersession chains

(None yet. First chain will appear when a v0.2 decision supersedes
a v0.1 assumption.)
