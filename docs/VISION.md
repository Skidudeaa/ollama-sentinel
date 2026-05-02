# Ollama Sentinel — Vision

Local-first development companion that learns how a codebase fails and uses that
memory to guide future change.

## What it actually is

Two modules in one repo, sharing a Python process and (in the v0.3 plan) a
schema substrate.

**Sentinel** — file watcher. On file save, sends the change to a local Ollama
model, parses the model's findings into structured rows, and persists them in
a SQLite memory with three layered recall strategies (semantic, structural,
single-file). The memory is the product, not the model.

**Research agent** — multi-step LangGraph workflow for dependency migrations,
CVEs, and API impact analysis. Produces ranked, file-and-line-specific impact
reports, not generic essays. Uses OpenAI; opt-in via `pip install -e ".[research]"`.

Both run on the user's machine. Code never leaves it (with the standard caveat
that the research agent does call OpenAI when invoked).

## State as of this session — v0.1.0+

Shipped, public on GitHub, 353 tests passing, ~2.4s suite. Recent work in this
session:

- **Structural recall wired into the sentinel's hot path.** The
  `ImportResolver` AST scanner — previously dead-coded behind a research-agent
  import — now augments prior-violation lookup with 1-hop import-graph
  neighbors. A finding on `utils.py` surfaces when reviewing `app.py`. A
  finding on `app.py` surfaces when reviewing `utils.py`. Layered after
  semantic recall, before single-file recall. Python-only. Five tests. Five
  green.
- **Truth-in-advertising gap closed.** The README's "knows your blind spots"
  claim is now load-bearing. Before this session, structural awareness was
  promised by docs and not delivered by code.

## What's still aspirational

The vision document gestures at a "software immune system" and a "guardrail
compiler." Neither exists yet. The substrate they would need does not exist
either. Specifically: the sentinel's memory currently contains only **model
opinions** — the LLM said something is concerning, and the LLM keeps saying it
on every re-read of the same span. Recurrence count over LLM opinion is the
model agreeing with itself N times. That's not knowledge. That's an echo.

The schema cannot today represent:

- The commit that introduced a finding.
- The commit that resolved it.
- The objective failure that confirmed the model's suspicion (or didn't).
- The blast radius — where the failure actually surfaced, vs. where the model
  flagged.
- The test gap — what ran and passed despite the failure.

`Finding.resolved` is a single bit. When it flips, all knowledge of the fix
vaporizes.

## Next state — v0.2: the Finding/Incident split

The next architectural move splits the schema into two nouns:

**Finding** — what the model says. LLM hypothesis. Cheap to produce, plentiful,
unverified. Current schema, kept as-is.

**Incident** — what objectively happened. A failing test, a runtime exception,
a manual confirmation, a fix-shaped commit linked to a Finding. Each Incident
references the Finding(s) it corroborates and carries the artifacts that prove
it: triggering commit SHA, surfaced symptom location, blast radius file list,
fix commit SHA, fix shape.

The promotion path is the product:

```
Finding (model opinion)
   ↓ [pytest plugin: a test fails on file:line that has an open Finding]
   ↓ [post-test hook: walk recent commits in import-graph neighborhood, attribute]
   ↓ [manual: ollama-sentinel confirm <finding_id>]
Incident (corroborated event)
   ↓ [≥3 incidents with same shape]
Pattern (project-specific guardrail)
```

The watcher stops being the canonical signal source. Git events become
canonical:

- `post-commit` — snapshot diff, link findings to commit SHA.
- `pre-commit` — surface incidents touching files in the staged change.
- `post-test` — on test failure, run reverse import-graph traversal to
  attribute blame.

The file watcher remains for streaming commentary as you type, but it's no
longer where memory comes from.

`gitpython` is already a core dependency. The infrastructure cost of this
pivot is near zero.

## Next state — v0.3: shared substrate between modules

Today the two modules are architecturally independent. That was the right v0.1
call. It's now the ceiling.

**Lift `ImportResolver` out of `research_agent/tools/` into shared infra.** The
sentinel now uses it; promote it. Add incremental invalidation so the cache
doesn't stale during long-running watcher sessions.

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
- **No autonomous agent guardrails until v0.3+.** The substrate is too thin
  today; auto-intervention would be nagware.
- **No multi-language for now.** Adding TypeScript/JS multiplies the AST
  surface 5x without enough Python signal yet to justify it.
- **No web UI.** Rich TUI is the right surface for a tool that lives on a
  single dev's machine.

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
