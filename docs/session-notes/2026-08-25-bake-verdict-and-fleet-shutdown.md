# 2026-08-25 — Bake verdict + fleet shutdown

The 2026-08-08 dogfood bake ran 17 days and is now **over**. Everything is
stopped and disabled on both machines. **The project is parked, not dead** —
the operator is moving to other work. All finding data is preserved.

## The verdict

The bake asked one question (from the 2026-08-08 note): *does the passive
layer catch things Claude Code misses, at acceptable noise?*

**Yes on signal. No on noise — and the noise is three cheap bugs, not a model
problem.**

### A measurement trap to avoid next time

The first pass at this verdict used "0 resolved / 0 dismissed / 0 incidents
across 18,864 findings" as the headline evidence of failure. **That metric was
structurally incapable of being anything but zero** — the corroboration paths
(pytest plugin, post-commit hooks on droplet repos) were explicitly listed as
*deferred* in the 2026-08-08 note and never installed. Engagement counters
measure nothing when the wiring that feeds them was never run. The bake
criteria were about *review quality*; judge on that axis.

### Signal: real, and of a kind that survives a human read

Sampled high/critical `bug`+`security` findings. On soma (a production
clinical app):

- `static/js/admissions/vendor/fishbone-panel.js:105` — **a zero-width space
  (U+200B) inside a regex alternative.** Nothing but a machine finds that.
- `static/js/census/components/problem-detail-panel.js:457` — XSS.
- `clinical/extractors/clinical_extractor.py:1049` —
  `"high" in str(event.get("value", 0))`, coercing a numeric into a string for
  a substring test to infer clinical severity.
- `static/js/admissions/components/section-sidebar.js:2129` — `matchAll` on a
  dynamically-built regex, throws on zero-width matches.
- `hyperdrive/router.py:129` — PHI access-control chain flagged as too layered
  to audit.

theater6 (Swift) is the same story: a `TerritoryFrameRef` id built from
`Int(timeIntervalSince1970)` (second-resolution collisions), a
`handleTimelapseMessage` stale-closure race, `normalizedImportURL` accepting
`ftp://` and hostless input, an off-by-one in flow-layout wrap.

**The memory layer demonstrably worked.** Model output carried annotations like
`STILL PRESENT (flagged 4× since 2026-08-10)` and a `Prior Issues Status`
table — prior-unresolved injection landed and the reviewer tracked persistence
across runs. That is the core thesis of the project working in production.

### Noise: measured, and it is a labeling problem

| | soma | theater6 |
|---|---|---|
| "critical" where the word appears **only inside quoted code** | **125/177 (71%)** | 7/14 (50%) |
| findings on non-source files (json/md/txt/css/yml/.gitignore) | **1,702 (12%)** | 6 |
| rows absorbed by duplicate signatures | 1,080 (8%) | 608 (13%) |

Causes, in leverage order — these are now items 1–4 in CLAUDE.md's "Pickable
next moves":

1. **`extractor.py:161-164` scans severity out of the code fence.** Whole-block
   substring match, `"critical"` first in dict order. Strip fenced/inline code
   before scanning. Same flaw in `_CATEGORY_KEYWORDS`.
2. **Legacy extraction never sets `verbatim_excerpt`** → `relocate_finding`
   returns `"stored"` (`sarif.py:88`) → `fix`/`prune`/`surface` are all inert.
   100% of theater6, ~97% of dictum.
3. **Watch scope too wide** — reviewing `.jsonl` analytics and `.md`.
4. **Dedup:** `persist_findings` keys on
   `(file, line_start, line_end, category)`; both drift under legacy
   extraction. Real but *small* — the bulk of 18,864 are genuinely distinct.

### Confound worth naming

Every project ran `kimi-k3:cloud`, and all `:cloud` models ignore the Ollama
`format` schema — so the entire bake exercised the **legacy regex extractor**,
not the grounded JSON path. Defects 1, 2 and 4 are all legacy-path defects. A
future re-run against a grounded local model would test a different system.

## Bake coverage (smaller than "six projects" suggests)

| Target | Findings | Ran | Note |
|---|---|---|---|
| soma | 14,267 | full 17 d | droplet, only one live at shutdown |
| theater6 | 2,864 | 4 d (Aug 8–12) | then stopped |
| dictum | 908 | partial | Mac |
| dwell | 678 | **never** | all rows date from June, pre-bake |
| dictumd | 111 | partial | droplet |
| feb8 | 108 | stopped Aug 11 | droplet |

**Do not read the gaps as pure neglect.** The operator ran a maintenance pass
on the droplet on 2026-08-24 that stopped services, so some of the "stale"
ages above are maintenance, not abandonment. The one unambiguous coverage
fact is **dwell**: every row it holds predates the bake, so it contributed
nothing regardless. The verdict rests on soma's full 17 days plus theater6's
4, which is ample for a quality judgement.

## Shutdown inventory (what was actually touched)

**Droplet (`root@100.84.74.15`)** — stopped **and disabled** (no boot restart):

- `ollama-sentinel-somanotes.service`
- `ollama-sentinel-feb8.service`
- `ollama-sentinel-dictum.service`
- `sentinel-dash.service` (dashboard now returns nothing at :8300)
- `ollama.service` — verified safe: `ss -tnp` showed **only** sentinel
  processes on `:11434`, and the sole non-sentinel `ollama` string in
  somaNotes source is `generate_handoff_index.py:743` excluding
  `.ollama_reviews` from indexing.

**Mac:**

- launchd heartbeat `com.thomasamosson.sentinel-heartbeat` booted out; plist
  renamed → `~/Library/LaunchAgents/com.thomasamosson.sentinel-heartbeat.plist.disabled`
  (had `RunAtLoad`, so renaming is what actually prevents relaunch at login).
- theater6's sentinel git hook renamed →
  `~/jan25/swiftMaps/theater6/.git/hooks/post-commit.sentinel-disabled`
  (it called `ollama-sentinel record-commit` on every commit).
- No `ollama-sentinel run` processes were alive to kill.
- **Ollama.app left running** — it is the general-purpose desktop app, not
  fleet infrastructure. Quit it from the menu bar if you want it off.

**Data: preserved, not deleted.** Every `memory.db` was
`wal_checkpoint(TRUNCATE)`'d after its writer stopped, so no state is stranded
in a `-wal` sidecar.

## Droplet install reconciled (was a split-brain footgun)

The droplet had **two disagreeing copies** of ollama-sentinel, and the git
checkout was the *stale* one — the trap being that it looks authoritative:

- `/root/ollama-sentinel` (git checkout) sat on `master @ 1b3c127`
  (2026-05-03), **143 commits behind** origin. No `guardrails.py`, no v0.3, no
  v0.2 pieces 4/5, no `4d1cd2c` extractor fix.
- `/root/ollama-sentinel-venv` (what actually ran) was **newer** than that
  checkout — it had `guardrails.py` and the extractor fix, so it was installed
  from somewhere else (rsync from the Mac, most likely), never from
  `/root/ollama-sentinel`.

Anyone opening `/root/ollama-sentinel` to see "what the fleet ran" would have
read 4-month-old code. Fixed 2026-08-25: fast-forwarded the checkout to
`origin/master` (clean tree, zero local-only commits, so `--ff-only`
succeeded) and reinstalled the venv from it. The two trees are now
byte-identical and match `origin/master`; `ollama-sentinel --help` verified
working; all units confirmed still stopped and disabled afterward.

**If you ever reinstall on that box, install from the checkout**
(`/root/ollama-sentinel-venv/bin/pip install /root/ollama-sentinel`) so this
cannot drift apart again. Note it is a *non-editable* install — a `git pull`
alone does not update what runs; it needs the `pip install` too.

## Verified NOT wired to break anything

- somaNotes' `post-commit` is **Cairn**, and `pre-commit` is **docs-guard** —
  neither is ollama-sentinel. feb8 and dictumd have no hooks at all. The
  sentinel only ever installed a hook in theater6.
- The `pytest11` entry point (`ollama_sentinel.pytest_plugin`) is registered in
  the pyenv interpreter, but `pytest_configure` returns immediately unless a
  project sets the `ollama_sentinel` ini key. **No watched project sets it** —
  checked `pytest.ini` / `setup.cfg` / `tox.ini` / `pyproject.toml` in all
  three Mac repos. Harmless as-is.
- All droplet production services confirmed still running after shutdown:
  `alltogether`, `cairn-backend`, `cairn-daemon`, `dictum`, `feb8`, `nginx`,
  `postgresql@16-main`, `mongod`, `redis-server`, `annotation-worker`,
  `defuddle`, `dialectic`, `glasshud`, `reading-room`, `cloudflared-feb8`.
- Droplet `.ollama_reviews/` dirs stay hidden via `.git/info/exclude` (never
  `.gitignore` — those trees deploy by `git pull`). Untouched.
- `~/.local/bin/watchers` left in place; it is inert now (its `run` actions
  start a watcher only if invoked explicitly).

## Waking it back up

Nothing was uninstalled — venvs, configs, guardrails and DBs are all intact.

```bash
# droplet
ssh root@100.84.74.15
systemctl enable --now ollama.service
systemctl enable --now ollama-sentinel-somanotes ollama-sentinel-feb8 ollama-sentinel-dictum
systemctl enable --now sentinel-dash          # http://100.84.74.15:8300 (tailnet only)

# mac
mv ~/Library/LaunchAgents/com.thomasamosson.sentinel-heartbeat.plist.disabled \
   ~/Library/LaunchAgents/com.thomasamosson.sentinel-heartbeat.plist
launchctl bootstrap gui/$UID ~/Library/LaunchAgents/com.thomasamosson.sentinel-heartbeat.plist
mv ~/jan25/swiftMaps/theater6/.git/hooks/post-commit.sentinel-disabled \
   ~/jan25/swiftMaps/theater6/.git/hooks/post-commit
watchers theater6 run                          # or dwell / dictum
```

**If you do restart it, land CLAUDE.md's "Pickable next moves" #1 and #3
first.** They are small, and they are the difference between 18,864 findings
nobody reads and a triaged queue.

## Repo state

- `master`, clean, in sync with `origin/master`.
- `pytest tests/ -q` green (run it for the live count — it drifts).
- No code changed this session. Docs only: this note + CLAUDE.md status and
  next-moves rewrite.
