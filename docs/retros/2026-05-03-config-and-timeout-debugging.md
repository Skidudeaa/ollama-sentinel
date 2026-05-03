# Retrospective — Config-Load + Embedding-Timeout Debugging

**Date:** 2026-05-03
**Work:** Diagnosed sentinel 404s + embedder cold-load timeouts on a
real run against `Noctober24_UNBLOCKED`. Shipped four commits
(`0f3242e` … `2aff6a1`) covering README rewrite, embedder timeout knob
in YAML, and a measurement-driven default.

## What happened

User ran `ollama-sentinel run` and saw walls of 404s from `/api/chat`
and `/api/embeddings`, plus `Semantic embedding unavailable` warnings.
Diagnosed the chain:

1. **Wrong YAML loaded.** Two `ollama-sentinel.yaml` files existed: the
   canonical one in the repo, and a stale local copy in
   `Noctober24_UNBLOCKED/` that predated the Phase A migration.
   `ollama-sentinel run` reads from cwd, so the stale one won.
2. **Models in stale YAML weren't pulled.** `gemma3:4b` (chat) and
   `nomic-embed-text` (embedding) both 404'd. Ollama returns 404 with
   body `{"error":"model 'X' not found"}`, but httpx logs status only,
   so it looked like an endpoint problem.
3. **Embedder timed out at 30s on first call.** Treated this as
   evidence cold-load took 30s+. Bumped default to 120s. Wrong
   inference — the log entry `aborting embedding request due to client
   closing the connection` was the smoking gun: the *client* gave up
   at 30s; Ollama never got the chance to finish.
4. **Real cold-load is ~6.4s in the worst realistic case.** Measured
   four scenarios on M2 Max. Right-sized default to 30s with ~4.7x
   margin against the realistic worst case.

Separate observation that came up at the end: review output itself is
currently low-signal — the model emits the AI-reviewer house style
(magic-numbers-into-enum, redundant-computation, ZStack-simplification)
plus stale numeric values that don't match disk content. Not fixed
this session; flagged for future work.

## What we learned

### Config-loading discipline

- **`ollama-sentinel run` reads `ollama-sentinel.yaml` from cwd, not
  from a repo-relative location.** Multiple project trees can each
  shadow the canonical YAML with their own stale copy. When users
  report odd behavior, ask which directory they ran from before
  reading the repo's YAML.
- **Schema migrations in YAML need migration of every YAML on every
  watched project, not just the one in the repo.** Phase A migrated
  `embedding.model: X` → `embedding.models.hot: X` and shipped a
  one-shot deprecation warning, but the warning fires once per process
  — easy to miss in a wall of 404 noise. The stale YAML in this user's
  project sat broken for ~2 days post-migration before being noticed.

### Reading Ollama logs

- **Ollama 404 means model-not-found, not endpoint-not-found.** The
  response body distinguishes the two: `{"error":"model 'X' not found"}`
  is the model case. httpx's status-only logging hides this.
- **`aborting embedding request due to client closing the connection`
  means the client gave up first, not the server.** Duration in the
  Gin log is wall-clock until the abort, not Ollama's actual work
  time. To measure server-side time, drive the request with a client
  timeout longer than any plausible server latency, then read the
  Gin log's duration.
- **`requested context size too large for model` and `flash attention
  enabled but not supported by model` are harmless** when they fire
  on the embedding model. Ollama caps context internally and disables
  FA per-model; comes from the daemon's global env defaults
  (`OLLAMA_CONTEXT_LENGTH`, `OLLAMA_FLASH_ATTENTION`) clashing with
  per-model capabilities.

### Cold-load has three regimes, not one

Measurements for `qwen3-embedding:4b` (~2.5 GB Q4_K_M) on Apple M2 Max:

| Scenario | Time | Notes |
|---|---|---|
| Warm-page-cache cold (model unloaded from VRAM, file in OS page cache) | ~2.2s | Test with `keep_alive:0` then immediate re-call |
| Freshly-purged cold (`sudo purge` + VRAM eviction) | ~2.0s | Page cache barely matters on M2 Max NVMe |
| **Natural-idle cold (5+ min KEEP_ALIVE timeout, ~7 min more idle, system memory pressure)** | **~6.4s** | The realistic worst case for users |

The natural-idle case is slowest because *all* of memory alloc, runner
startup, *and* embedding compute slow down under system memory
pressure (free RAM dropped 25.4 → 22.5 GiB between calls in our
measurement). The contributors are not just disk read.

### Default-tuning discipline

- **Don't bump a default on a single anomalous data point.** I bumped
  the embedder timeout 30s → 120s on the strength of one observed 30s
  failure that I hadn't actually traced to a cold-load. It took two
  iterations and a measurement before settling on a defensible 30s.
- **Defaults should fail-fast on genuine hangs.** A 120s timeout
  surfaces "Ollama is wedged" only after two minutes of silent
  retry-storm; 30s catches it in reasonable time without false
  positives on natural-idle cold-loads.
- **Ship the YAML knob alongside the default-tuning commit, not
  afterward.** Users on slower hardware shouldn't need a code change
  to escape a too-tight default.

### What we did NOT fix

The reviewer model emits generic AI-review boilerplate
(magic-numbers/redundant-computation/ZStack-simplification) regardless
of whether those issues exist in the file under review, and
occasionally cites stale numeric values that don't match disk content.
Two leverage points exist (sharper system_prompt requiring verbatim
quotes; post-hoc finding-validator against file content), but neither
was in scope this session. Open issue.

## Configuration changes shipped

- `0f3242e` — README split into one-time setup vs each-time use, with
  cwd-matters callout and 404 troubleshooting row.
- `bf3012f` — *(later reverted)* embedder default 30s → 120s on bad
  inference. Kept in history as a tombstone.
- `b942035` — `embedding.timeout_seconds` exposed as YAML knob with
  positive-int validator.
- `2aff6a1` — embedder default right-sized to 30s based on the 6.4s
  natural-idle measurement; comment cites all three regimes.

## Open follow-ups

- Sentinel review-quality regression. Reviews are pattern-matched
  rather than file-grounded. Documented here; not in scope.
- Two-yaml shadow problem. No mitigation today. Could surface a
  startup banner showing the resolved YAML's absolute path so
  multi-project users notice they're loading the wrong one.
