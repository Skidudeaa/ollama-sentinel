# Session handoff — 2026-06-29

## TL;DR

Diagnosed a recurring `/api/embeddings 500` the user saw running the sentinel
live, fixed it with a startup embedder pre-warm, and shipped it (plus the
already-committed init model auto-detect) straight to `master`. Repo is clean and
in sync with `origin/master @ 4270b9b`. Suite green (830 / 16).

## What landed

1. **Startup embedder pre-warm** (`4270b9b`, this session)
   - `ollama_sentinel/processor.py` — new `FileProcessor.prewarm_embedder()`:
     issues one throwaway `embed("warmup")` so Ollama resident-loads the hot
     embedding model while memory is free. No-op when no embedder is configured;
     catches `EmbeddingUnavailable` and logs a warning instead of raising
     (best-effort — startup never breaks, recall just degrades as it already
     does at review time).
   - `ollama_sentinel/watcher.py` — `FileSentinel.run()` calls
     `await self.processor.prewarm_embedder()` immediately before the watch loop.
   - Tests: `tests/test_processor.py::TestPrewarmEmbedder` (issues request /
     no-op without embedder / swallows 500), `tests/test_watcher.py::
     TestRunPrewarmsEmbedder` (run() pre-warms before watching).

2. **init model auto-detect** (`e7cb5aa`) — already committed on the old
   `feat/init-auto-detect-model` branch; rode in on the same fast-forward.

## The diagnosis (why pre-warm)

The user's log showed `POST /api/embeddings → 500` twice, then `POST /api/chat
→ 200` ~46s later. That 46s gap was the cold-load of `qwen3-coder:30b` (≈19 GB
on GPU). The embedder (`qwen3-embedding:4b`, 2.5 GB) tried to load *concurrently*
with the big review model under unified-memory pressure → Ollama returned 500.
The app degraded gracefully (`using identity order`) and the review still
completed — so it was never a code bug, just a memory-fit race on the first file
after a cold model load. Pre-warming loads the embedder first, while memory is
free, so the race window is gone.

Confirmed the fix works at the unit level (`ollama ps` showed
`qwen3-embedding:4b` resident after a `prewarm_embedder()` call against the real
Ollama).

## Open follow-up (the one real TODO)

- **Re-confirm live in the real watched project.** Restart the watcher against
  `Noctober24_UNBLOCKED` and verify: (a) `Embedding model qwen3-embedding:4b
  pre-warmed.` appears at startup, and (b) the first file change no longer logs
  `/api/embeddings 500`. That's the end-to-end proof; only the unit/smoke level
  was verified this session.

## Git / housekeeping notes

- `master` was force-rebased onto `origin/master` this session because origin had
  diverged: 6 unrelated commits (`b05a811`..`4bde766`, a *"Sturgeon Lake"
  temporary build workflow*) had been pushed to master by accident. They net to
  an **empty diff** (a 180-line `.sturgeon_map_work/build_sturgeon_source.py` was
  added then removed) — harmless history noise, left as-is (not worth a public
  force-push to scrub). The recovered script was moved out to
  `/Users/thomasamosson/jan25/BLACnativeARglasses/build_sturgeon_source.py`
  (a GIS/bathymetry project — unrelated to this repo).
- `feat/init-auto-detect-model` deleted local + remote.

## Resume here next time

1. `pytest tests/ -q` green, `git status` clean on `master`.
2. Do the live re-confirm above if you want the 500-is-gone proof.
3. Otherwise the pickable-next-moves table at the top of `CLAUDE.md` is unchanged
   (guardrail deferrals; the v0.3 shared-substrate arc).
