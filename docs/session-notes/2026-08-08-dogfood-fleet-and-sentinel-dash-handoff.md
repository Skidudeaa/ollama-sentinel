# 2026-08-08 — Dogfood fleet launched + sentinel-dash shipped

## What this session was

The project's stated problem was "grown stale, no tangible benefit to my
other projects." The answer chosen: **stop building features, force an
adoption verdict.** By session end, ollama-sentinel watches **six real
projects across two machines**, all reviewed by `kimi-k3:cloud` via Ollama
cloud (the user's deliberate choice), with a Tailscale-private web dashboard
monitoring the whole fleet.

## The fleet

| Target | Where | Path | Watcher |
|---|---|---|---|
| theater6 | Mac | `~/jan25/swiftMaps/theater6` | manual (`watchers theater6 run`) |
| dwell | Mac | `~/jan25/Noctober24_UNBLOCKED` (iOS music app "Dwell") | manual |
| dictum | Mac | `~/jan25/Dictum` (Swift client) | manual |
| soma | droplet | `/root/somaNotes` — **working tree IS production** | systemd `ollama-sentinel-somanotes` |
| feb8 | droplet | `/root/jan25` (feb8Quart music backend — NOT the Mac's `~/jan25`) | systemd `ollama-sentinel-feb8` |
| dictumd | droplet | `/root/dictum` (FastAPI backend for dictum) | systemd `ollama-sentinel-dictum` |

Droplet installs are strictly additive: sentinel in its own venv
(`/root/ollama-sentinel-venv`), configs + `.ollama_reviews/` hidden via
`.git/info/exclude` (never `.gitignore` — the droplet trees deploy by git
pull). Every project got 4–5 guardrails derived from its own CLAUDE.md, and
the two client/server pairs (Noctober24↔feb8, Dictum↔dictumd) carry
**matched cross-repo guardrails grounded in verified contract facts**
(`active_until_year` phase divergence; WebSocket auth three-transport rule).

## Code landed in THIS repo

- `4d1cd2c` **fix(extractor): accept terse `L31` / `L36–38` line refs.**
  kimi-k3 references lines that way; `_LINE_REF_PATTERN` matched nothing and
  reviews silently persisted zero findings. Case-sensitive `\bL` alternative
  (`l33t`/`URL31` stay non-matches), 3 tests from verbatim K3 output.
  Suite 833 passed / 16 skipped. Pushed.

## kimi-k3:cloud operating facts (verified live)

1. Ignores the Ollama `format` JSON schema (all `:cloud` models do) → run
   with `processing.grounding: false`; legacy extractor handles the prose.
2. Reasoning model: thinking tokens count against `num_predict`. A 4096
   reserve produced a 47-byte review. Use `output_reserve_tokens: 16384`
   (+ `think: false`, though it may be ignored) and `context_window: 65536`.
3. Line-ref style drifts run to run (`L31` vs `Line 31`) — the extractor
   fix covers both.
4. Review quality on Swift is excellent (found real ACLED API misuse).
   Occasional reviews run 8+ minutes; `request_timeout: 600` is right.
5. Embedder pre-warm (`4270b9b`) confirmed live — no `/api/embeddings` 500
   on first review. That old follow-up is CLOSED.

## sentinel-dash (separate repo: `Skidudeaa/sentinel-dash`, private)

Tailscale-only dashboard at **http://100.84.74.15:8300** (bound to the
droplet's tailscale IP; `tailscale serve` not enabled on the tailnet —
enabling it is an optional upgrade). FastAPI aggregator reads droplet
`.ollama_reviews/` directly every 5s; Mac projects arrive via launchd
heartbeat (`com.thomasamosson.sentinel-heartbeat`, 120s, stdlib-only
collector). SSE + poll fallback. Cards show top-6 open findings in triage
order, severity histograms, resolution history. Easter eggs: Sentinel Eye,
Konami→synthwave, mascots, test-failure klaxon (one-shot delta, verified).
Ops CLI on the Mac: `watchers` (`~/.local/bin/watchers`) — targets
`dash|theater6|dwell|dictum|soma|feb8|dictumd|all`.

## The verdict clock

**Started 2026-08-08, review ~2026-08-22.** Question: does the passive layer
catch things Claude Code misses, at acceptable noise? Baseline at launch:
~1,225 open findings fleet-wide (dwell 678, theater6 259, soma 213 w/ 1
critical, dictum 28/49, dictumd 13, feb8 5). If the answer is no: strip to
the parts that earned their keep, or archive. `research_agent` is slated for
cutting regardless (OpenAI/LangChain dead weight vs the local-first pitch).

## Deferred / open

- pytest plugin + post-commit hooks on the droplet projects (need installs
  into PROD venvs / repos — explicit opt-in, one command each).
- somaNotes' 1 critical finding — surfaced by the dashboard, uninvestigated.
- theater6/Noctober sentinel yamls are untracked in their repos (their
  owners' call whether to commit them; Dictum's is committed).
- `tailscale serve` enablement for an HTTPS MagicDNS dashboard URL.
