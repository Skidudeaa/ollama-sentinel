# Ollama Sentinel — Vision

Local-first development companion that learns how a codebase fails and uses that
memory to guide future change.

## What it actually is

Two modules in one repo, sharing a Python process and (in the v0.3 plan) a
schema substrate.

**Sentinel** — file watcher. On file save, sends the change to a local Ollama
model, parses the model's findings into structured rows, and persists them in
a SQLite memory with three layered recall strategies (semantic, structural,
single-file). It also carries a **guardrail** layer — curated, named rules
injected into every relevant review, authored directly or auto-promoted from
recurring corroborated failures. The memory is the product, not the model.

**Research agent** — multi-step LangGraph workflow for dependency migrations,
CVEs, and API impact analysis. Produces ranked, file-and-line-specific impact
reports, not generic essays. Uses OpenAI; opt-in via `pip install -e ".[research]"`.

Both run on the user's machine. Code never leaves it (with the standard caveat
that the research agent does call OpenAI when invoked).

## Where it is now

**Parked as of 2026-08-25, after a 17-day trial on six live projects.** The
code is shipped and public, the suite is green (`pytest tests/ -q` — quote the
command, not a count), and nothing is running. Read
[the verdict](session-notes/2026-08-25-bake-verdict-and-fleet-shutdown.md)
before treating anything below as a plan.

The short version, because it changes how to read this document: **the review
engine and the memory both worked; the economics did not.** 18,864 findings in
17 days is ~1,100/day against one developer. The vision below is intact as a
description of what was built and why. It is *not* a roadmap anyone is
currently executing.

The product moved well past the v0.1 "model that comments on your code" stage.
Four things are true today that were aspirational a few months ago:

- **The memory is layered and grounded.** Prior-violation recall is semantic
  (embedding neighbors via `qwen3-embedding:4b` on the hot path — see Phase A
  below), structural (1-hop import-graph neighbors via `ollama_sentinel/context/import_resolver.py`
  across Python `.py`, TypeScript/JavaScript `.ts`/`.tsx`/`.js`/`.jsx`, and Swift `.swift`,
  so a finding on `utils.py` or `utils.ts` surfaces when reviewing `app.py` or `app.ts`), and
  single-file. **The trial confirmed this one empirically** — reviews came back
  carrying `STILL PRESENT (flagged 4× since 2026-08-10)` and prior-issue status
  tables, i.e. the reviewer was demonstrably reasoning over its own history.
  What the trial did *not* support was the older "after a month it's
  indispensable" framing: recall working is necessary, not sufficient.
- **Findings are no longer just opinions.** The v0.2 Finding/Incident split
  gives the memory a way to record what *objectively happened* — a failing
  test, a fix-shaped commit, a manual confirmation — not just what the model
  said. (Detailed below.)
- **Findings are actionable.** A finding is no longer a dead row in a table.
  It can surface in your editor and CI, be auto-fixed, be triaged from tool
  output, and be pruned when the code it flagged is gone. (The "make findings
  actionable" arc, below.)
- **Failure history becomes a rulebook.** The Finding → Incident → **Pattern**
  rung is closed: recurring corroborated failures harden into **guardrails** —
  named rules the reviewer checks on every future change — either authored by
  hand or auto-promoted from a shape seen ≥3 times. The north star below is no
  longer aspirational; injection is how "every future diff is reviewed with that
  history as context" actually happens. (Detailed below.)

**Phase A — hot-path embedding swap (shipped 2026-05-01).** The semantic
recall embedder moved from `nomic-embed-text` to `qwen3-embedding:4b`.
`EmbeddingConfig` is now a named-role dict; the `consolidation` and `rerank`
roles are pre-registered in the schema but intentionally unwired (Phases B/C
are parked — no demand yet, and the plan forbids pulling those models
speculatively). Legacy `embedding.model: foo` YAML auto-migrates with a
one-shot deprecation warning.

## From opinion to event — the Finding/Incident model (v0.2, shipped)

The v0.1 memory contained only **model opinions**: the LLM said something was
concerning, and kept saying it on every re-read of the same span. Recurrence
count over LLM opinion is the model agreeing with itself N times. That's not
knowledge — that's an echo. The v0.1 schema could not represent the commit that
introduced a finding, the commit that resolved it, the objective failure that
confirmed (or refuted) the suspicion, the blast radius (where the failure
surfaced vs. where the model flagged), or the test gap. When `Finding.resolved`
flipped, all knowledge of the fix vaporized.

v0.2 closes those gaps by splitting the schema into two nouns:

**Finding** — what the model says. LLM hypothesis. Cheap to produce, plentiful,
unverified. The v0.1 schema, kept as-is.

**Incident** — what objectively happened. A failing test, a manual
confirmation, a fix-shaped commit linked to a Finding. Each Incident
references exactly one Finding and carries the artifacts that prove the
corroboration: the confirming signal and artifact, a best-guess triggering
commit plus a ranked `suspect_commits` list when attribution is ambiguous, the
surfaced symptom location, an optional blast-radius file list, and (for fixes)
the fix commit and fix shape. Incidents are never upserted — each row is a
distinct event, so one Finding can accrue several independent corroborations.

The promotion path is the product:

```
Finding (model opinion)
   ↓ [pytest plugin: a test fails on file:line that has an open Finding]
   ↓ [post-commit hook: link the commit to open Findings in touched files]
   ↓ [manual: ollama-sentinel confirm <finding_id>]
Incident (corroborated event)        ← inspect with `ollama-sentinel incidents`
   ↓ [≥3 distinct corroborated findings of the same shape, you confirm]
Pattern (project-specific guardrail) ← shipped v0.3; `ollama-sentinel guardrail …`
```

What shipped in v0.2:

- **Incident schema + migration.** `incidents` table and two nullable
  `findings` columns, added idempotently on open. Populated DBs upgrade with
  no data loss or required user action.
- **pytest plugin.** Opt-in (`ollama_sentinel = true` in the project's pytest
  config); on a test failure it matches the crash location to open Findings
  within a ±tolerance window and records a `test_failure` Incident. Zero-cost
  when inactive.
- **`confirm` verb.** Manual corroboration — records a `manual_confirm`
  Incident; the Finding stays open.
- **post-commit hook** (`install-hooks` / `record-commit`). Links a commit to
  open Findings in the files it touched, recording the triggering SHA.
- **`incidents` verb.** Lists corroborated events (table or JSON), optionally
  scoped to one Finding.

The watcher is no longer the only signal source — git and test events now feed
memory too. The file watcher remains for streaming commentary as you type.
`gitpython` was already a core dependency, so the infrastructure cost of this
pivot was near zero.

Deferred past v0.2: `pre-commit` surfacing of incidents on staged files
(v0.2.1) and reverse import-graph blame traversal beyond the simple
`suspect_commits` heuristic (v0.2.1). The third deferral — ≥3-incident Pattern
promotion — **shipped in v0.3** (the guardrail layer, below).

## Making findings actionable (shipped)

v0.2 made findings *corroborable*. The actionability arc (shipped and merged
2026-06-04/05) makes them *operable* — a finding becomes something you can see,
diagnose, fix, and retire, from the command line and from inside your editor.
Four verbs, plus their supporting cast:

- **`surface`** — emit open Findings to `.ollama_reviews/findings.sarif`
  (SARIF 2.1.0). They light up in the editor Problems panel and in CI, with
  excerpt-based relocation so a finding tracks its code even as surrounding
  lines shift. The watcher also auto-refreshes the SARIF file.
- **`triage`** — pipe tool output (pytest, mypy, ruff, a traceback, anything)
  into a local-model diagnosis. File+line references are auto-extracted from
  the output and the relevant source is pulled in as context, so the model
  reasons about *your* failure, not a generic one.
- **`fix <id>`** — a localized, excerpt-verified fix for one Finding. It
  previews a diff, writes only on confirm (`[y/N]` or `--yes`), and resolves
  the Finding as fixed. The write is bounded to the finding's exact whole-line
  span, atomic, UTF-8/CRLF-preserving, and mode-preserving. It never commits.
- **`prune`** — close Findings whose flagged code is gone: the file no longer
  exists, or the verbatim excerpt no longer relocates. Preview + confirm, then
  close with `resolution='stale'` (no Incident — a vanished finding isn't a
  corroborated event). Read-only on source. On top of explicit `prune`, saving a
  watched file now automatically runs `reconcile_file_findings` before review
  generation, closing vanished findings (`resolution='stale'`) and updating
  shifted line spans in place.
- **`check`** — review all git-modified/untracked or staged (`--staged`) files
  in one command, with an optional `--fail-on <severity>` exit gate for
  pre-commit hooks and CI.

Supporting verbs round out the lifecycle: `findings` (list open Findings with
ids), `resolve` / `dismiss` (close as fixed / false-positive, idempotent), and
`dashboard` (a live Rich TUI of recent reviews and recurring violations that
also supports interactive triage via `r`/`d`/`c` and excerpt-bounded fix
preview/apply via `f`/`y`/`n`).

The throughline: a Finding now has a full lifecycle — proposed by the model,
corroborated by events, surfaced where you work, acted on, and retired — rather
than accumulating as inert rows.

## From event to rule — the guardrail layer (v0.3, shipped)

Corroboration (v0.2) told the memory what *objectively happened*. The guardrail
layer closes the loop: it turns a recurring corroborated failure into a
**durable, forward-looking rule** that shapes every future review. This is the
**Pattern** rung of Finding → Incident → Pattern, and it's the compounding
payoff the whole schema was built toward.

A **guardrail** is a curated, named natural-language assertion (`"Never call
eval/exec on untrusted input."`) with optional scope (a finding category and/or
a path glob). It is *checked by the review model against the code* — there is no
deterministic regex/AST engine, which keeps the matcher flexible. Guardrails are
born two ways and converge on one active artifact:

- **Manual authoring (the day-one path).** `guardrail add` creates an active
  rule immediately, with zero incident history. Pure auto-promotion stalls
  because corroborated incidents accrue slowly, so manual authoring is
  first-class — the value loop works on a fresh DB.
- **Auto-promotion (the compounding layer).** On demand, `guardrail candidates`
  selects corroborated findings, clusters them by category and embedding
  similarity, and surfaces any shape with ≥3 distinct corroborated findings as a
  candidate with an LLM-drafted assertion. `guardrail promote` confirms it into
  an active rule; `guardrail reject` suppresses the shape. Clustering reuses the
  existing `qwen3-embedding:4b` hot-path embedder and runs **only** in these
  commands — never on the watcher or dashboard loop, so it never taxes review
  latency.

Active guardrails are scope-filtered and relevance-ranked into the review prompt
(reusing the token-budgeted context assembler, alongside prior-violation recall),
and findings they produce carry the originating guardrail's **provenance**.

Two integrity properties make this safe rather than nagware:

- **Curation gates enforcement.** Nothing becomes an enforced rule without an
  explicit human confirm. A candidate is a proposal.
- **No self-manufactured evidence.** A guardrail flags findings tagged with its
  provenance; those findings could otherwise cluster back into a candidate that
  re-proposes the same rule — a Pattern-tier echo. The **evidence-integrity
  gate** blocks it: a guardrail's own findings reinforce a candidate only via a
  *hard* signal (a `test_failure` or `fix_commit` Incident), never a bare
  opinion or a manual confirmation. Independently-discovered findings count
  normally, and a missing provenance link fails safe (counts as independent).

The result: the codebase's failure history stops being a log you read and
becomes a rulebook the model enforces — exactly the north star below.

## What's still aspirational — v0.3: shared substrate between modules

> **Updated 2026-11-02:** The extraction/presentation bottlenecks identified by
> the 17-day trial — severity keyword scanning over quoted code, missing
> `verbatim_excerpt` on the legacy regex path, stale findings lingering across
> saves, and single-language structural recall — have been fixed (see the
> [next-moves table](../CLAUDE.md)). `ImportResolver` has also been lifted into
> `ollama_sentinel/context/import_resolver.py` with Python, TypeScript/JS, and
> Swift support.
>
> `research_agent` was long slated for cutting (OpenAI + LangChain dead weight
> against a local-first pitch). **That was reversed 2026-08-25 — it stays.**
> Which matters here: it is one half of the substrate this section proposes
> unifying, so the idea below remains structurally possible rather than
> orphaned. It is also, still, untouched by the trial: nothing in 17 days
> exercised it.

Today the two modules are architecturally independent. That was the right v0.1
call. It's now the ceiling.

**Lift `ImportResolver` out of `research_agent/tools/` into shared infra (shipped for sentinel 2026-11-02).**
`ollama_sentinel/context/import_resolver.py` now provides multi-language 1-hop
import resolution (`.py`, `.ts`/`.tsx`/`.js`/`.jsx`, `.swift`) with per-file
`invalidate(file_path)` cache refresh on save, without reaching into
`research_agent`.

**Unify `Finding` and `ImpactItem`.** Both describe the same noun — a
concerning location in code — through different lenses. Common base:
`(file_path, line_start, line_end, severity, body)` plus subtype-specific
fields. `ImpactItem` becomes a synthetic Finding tagged
`source=research_agent`, written into the violation DB so the next review of
that file already carries threat context.

**Bidirectional flow.** When `impact_scan` flags a file as HIGH-risk for a
CVE, it writes into the sentinel's memory. When the research agent scopes
impact for a proposed migration, it reads the sentinel's incident history —
"these break-sites have prior incidents, weight them higher in the ranking."

This is the move that makes the moat actually moat-shaped. Two interesting
tools that share a process becomes one thing that has no external equivalent.

## Explicit non-goals

- **No fine-tuning.** The leverage is in schema, not model.
- **No *autonomous* enforcement.** The shipped guardrails (above) are **curated**
  — nothing becomes an enforced rule without a human confirm, and a guardrail
  surfaces concerns into the review, it does not block, rewrite, or act on its
  own. Auto-intervention without curation would be nagware; that line stays.
- ~~**No multi-language for now.**~~ **Relaxed 2026-11-02.** `ImportResolver` in
  `ollama_sentinel/context/import_resolver.py` now supports Python AST,
  TypeScript/JavaScript relative/barrel imports, and Swift module-sibling
  resolution so structural recall works across all three stacks.
- ~~**No web UI.**~~ **Broken, knowingly, in 2026-08.** Running six watchers
  across two machines made a per-machine TUI useless, so the trial shipped
  `sentinel-dash` (separate private repo: FastAPI + SSE, tailnet-only) to see
  the whole fleet at once. The non-goal was right for the single-machine case
  and wrong for the fleet case. It is stopped and disabled now, along with
  everything else. Recorded here rather than quietly deleted, because a
  non-goal that got violated is more informative than one that held.

## North star

> A local incident memory for a codebase: every bug becomes a structured
> record linking the change that caused it, the symptom that surfaced, and the
> test gap that let it through — and every future diff is reviewed with that
> history as context.

Smaller than "software immune system." Buildable. Novel. Nobody is doing
structured causal incident capture at the repo level for solo devs.

## What this is not

Not a better code reviewer. Reviewers exist. Not a dashboard. Dashboards
exist. Not an "AI memory" product — that's a category full of vector stores
that forget what they were for.

The category is **local failure-mode intelligence**: the codebase
accumulates operational memory about its own weak points, and uses that memory
to make humans and AI agents less likely to repeat known mistakes. The
codebase becomes less amnesiac.

That's the product. Everything else is plumbing.
