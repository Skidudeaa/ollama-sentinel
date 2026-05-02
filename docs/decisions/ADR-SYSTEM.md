# Architecture Decision Records — System Design

This document defines how ollama-sentinel captures, links, and navigates
its own architectural evolution. It is the ADR system's ADR.

---

## The problem it solves

Five things go wrong with project documentation over time:

1. **Decisions vanish.** Why did we pick SQLite over Postgres? Why is the
   ImportResolver in research_agent/ instead of shared/? The answer lived
   in a Claude conversation that's gone.
2. **Vision docs fossilize.** VISION.md says one thing; the code does
   another. Nobody updates the doc because nobody knows which parts are
   still true.
3. **Plans outlive their relevance.** The four-phase Qwen3 plan was
   correct when written, wrong by the time Phase A shipped (five
   deviations). The plan file still exists, now misleading.
4. **Retrospective knowledge evaporates.** CB-3's agent made two
   architectural calls (closure-grep guards, leaf-module relocation)
   that are now precedent. Nothing records them as precedent except a
   chat transcript that will scroll off.
5. **Navigation is linear.** `git log docs/` gives you time-ordered
   changes. It doesn't give you "show me every decision about the
   embedding pipeline" or "what changed between v0.1 and v0.2."

The system below fixes all five with markdown files, git, and one
index file that a human or agent maintains.

---

## Three document types

### 1. Decision Records (`docs/decisions/NNNN-short-title.md`)

**What they capture:** A single architectural decision — what was chosen,
what was rejected, and why. One decision per file. Never edited after
acceptance; superseded by a new record that references the old one.

**Lifecycle states:**
- `proposed` — under discussion, may change
- `accepted` — decided, now load-bearing
- `superseded by NNNN` — a later decision replaced this one
- `deprecated` — still in effect but scheduled for removal (with version)

**Numbering:** four-digit, zero-padded, monotonically increasing.
`0001`, `0002`, etc. Gaps are fine (deleted proposals don't renumber).

**Template:**

```markdown
# NNNN — Title

**Status:** accepted
**Date:** YYYY-MM-DD
**Supersedes:** (none | NNNN)
**Superseded by:** (none | NNNN)
**Tags:** embedding, schema, recall, hooks, config, testing, process
**Commit:** (SHA that implemented this, added after merge)
**PR:** (number, if applicable)

## Context

What forces are at play. What problem needed solving. What constraints
existed. 2-5 sentences.

## Decision

What we chose. Be specific — name the file, the function, the pattern.
1-3 sentences.

## Consequences

What follows from this decision. What becomes easier. What becomes
harder. What technical debt is accepted. 2-5 sentences.

## Alternatives considered

What we didn't pick and why. One line per alternative is enough.
```

**Rules:**
- A decision record is immutable after `status: accepted`. If you learn
  the decision was wrong, write a new record that supersedes it.
  Update the old record's header to `superseded by NNNN` — that's the
  only edit ever made to an accepted record.
- Tags are drawn from a fixed vocabulary (see the index). New tags
  require adding them to the index first. This prevents tag sprawl.
- The `Commit` field is filled *after* the implementing PR merges, not
  before. This links the decision to the code that enacted it.

### 2. Living Documents (`docs/VISION.md`, `docs/GUIDE.md`, etc.)

**What they capture:** Current-state knowledge that evolves in place.
The vision, the user guide, the architecture overview.

**Rules:**
- Edited in place. Git provides the longitudinal record.
- Every edit to a living document is committed with the prefix
  `docs(living):` so `git log --grep="docs(living)" docs/VISION.md`
  gives the evolution history.
- Living documents reference decision records by number:
  "see [ADR-0003](decisions/0003-structural-recall.md)" — not by
  restating the decision's content. The decision record is the
  authority; the living document is the navigator.
- A living document must not exceed a word limit. VISION.md: 2500
  words. GUIDE.md: 5000 words. When an edit would exceed the limit,
  prune older content into a decision record or a retrospective
  before adding new content. This is the Cairn Constraint
  operationalized: the document stays useful for thinking forward
  because it's forced to shed weight.

### 3. Retrospectives (`docs/retros/YYYY-MM-DD-short-title.md`)

**What they capture:** What a piece of work *taught us* — patterns
discovered, spec deviations that turned out to be correct, process
failures, precedents established. Written after work ships, not before.

**Template:**

```markdown
# Retrospective — Title

**Date:** YYYY-MM-DD
**Work:** (what shipped — PR number, commit range, or plan file)
**Decisions produced:** (list of ADR numbers this retro generated)

## What happened

The work, in 2-3 sentences.

## What we learned

Bullet list. Each item is a concrete observation, not a feeling.

## Precedents established

Patterns that future work should follow. Name the file and function
where the pattern lives.

## What we'd do differently

Bullet list. Each item is actionable.
```

**Rules:**
- Retrospectives are written *after* the work ships. Not during, not
  before.
- Any precedent worth preserving as policy becomes a decision record.
  The retrospective names the precedent; the ADR formalizes it.
- Retrospectives are never edited after creation. They're snapshots
  of what we knew at the time.

---

## The index (`docs/decisions/INDEX.md`)

The index is the non-linear navigation surface. It provides three views
into the same set of decision records:

**By number** — canonical order, every ADR in one table.

**By tag** — topic-first navigation. "Show me every decision about
embeddings" is a single heading scan. Tags are a closed vocabulary
maintained in the index itself.

**By version** — temporal landmarks. "What decisions shaped v0.2.0?"
is a single section.

**Supersession chains** — when a decision replaces another, the chain
is visible in one place.

The index is the *one* file in `docs/decisions/` that gets edited
after creation. Every accepted decision record must appear in all
three views. Missing entries are bugs.

---

## How it links to the code

Three linking mechanisms, each serving a different query direction:

**Decision → Code (forward link):** The `Commit` and `PR` fields in
each decision record point at the implementing code. "What code enacted
this decision?" → read the ADR's header.

**Code → Decision (backward link):** When a code pattern exists because
of a specific decision, a one-line comment references it:
```python
# ADR-0004: closure-grep guard pattern — test wiring via source grep
# rather than monkeypatch when the closure can't be unit-constructed.
```
These comments are grep-able: `grep -rn "ADR-" ollama_sentinel/ tests/`
gives every code location tied to a decision.

**Version → Decisions (temporal link):** The index's "by version"
section maps release tags to decision ranges. "What decisions shaped
v0.2.0?" → read the index.

---

## The commit convention

All documentation changes use a scoped prefix:

| Prefix | When to use |
|---|---|
| `adr(NNNN):` | Creating or superseding a decision record |
| `docs(living):` | Editing VISION.md, GUIDE.md, or other living docs |
| `retro:` | Adding a retrospective |
| `docs:` | Everything else (plans, notes, index updates) |

This makes `git log --grep` the primary longitudinal query tool:

```bash
# Every decision ever made
git log --oneline --grep="adr("

# Evolution of the vision
git log --oneline --grep="docs(living)" -- docs/VISION.md

# All retrospectives
git log --oneline --grep="retro:"

# Everything about embedding decisions
git log --oneline --grep="adr(" -- docs/decisions/*embed*
```

---

## Directory structure

```
docs/
├── VISION.md                          # living document, ≤2500 words
├── GUIDE.md                           # living document, ≤5000 words
├── index.html                         # visual pitch (existing)
├── decisions/
│   ├── ADR-SYSTEM.md                  # this file
│   ├── INDEX.md                       # the three-view navigator
│   ├── 0001-short-title.md
│   ├── 0002-short-title.md
│   └── ...
├── retros/
│   ├── 2026-05-02-cb3-phase-a.md
│   └── ...
└── superpowers/                       # existing — plans, specs, notes
    ├── plans/
    ├── specs/
    ├── notes/
    └── followups.md
```

`superpowers/` stays as-is. Plans and specs are *pre-decision*
artifacts — they describe what we intend to do. Decision records are
*post-decision* artifacts — they describe what we chose. Retrospectives
are *post-work* artifacts — they describe what we learned. The three
directories capture the full lifecycle: intent → decision → reflection.

---

## Maintenance cost

Designed for a solo developer who works in bursts with weeks of silence
between them.

**Per decision:** ~10 minutes to write the ADR. ~2 minutes to update
the index. One `adr(NNNN):` commit.

**Per shipped piece of work:** ~15 minutes for the retrospective. ~5
minutes to update the index's version section. One `retro:` commit.

**Per VISION.md edit:** whatever the edit takes, plus a word-count
check. One `docs(living):` commit.

**When you don't work for three weeks:** nothing rots. ADRs are
immutable. The index is stale only if decisions were made without
updating it — and the commit convention lets you catch up:
`git log --oneline --grep="adr(" --since="3 weeks ago"` shows what
was decided while you were away.

**When an agent picks up the project:** they read INDEX.md first,
then the ADRs tagged with whatever they're working on, then the
relevant living doc. That's the non-linear entry: topic-first, not
time-first. The agent doesn't need to read every ADR — the tag
index tells them which ones matter for their task.

---

## What this is not

- **Not a wiki.** No hyperlink graph to maintain. Cross-references
  are ADR numbers in plain text, grep-able.
- **Not a knowledge base.** Doesn't try to capture everything known
  about the project. Captures *decisions* and *lessons*. Everything
  else lives in the code, the tests, and the living docs.
- **Not a tool.** No CLI, no database, no build step. Markdown files,
  git commits, grep.
- **Not Cairn.** Cairn captures the full reasoning layer — sessions,
  events, AI synthesis. This captures the *decision* layer only. The
  subset of Cairn's thesis that survives without a server.
