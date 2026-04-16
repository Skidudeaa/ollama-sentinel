# `ollama-sentinel triage` — Design Spec

**Date:** 2026-04-16
**Status:** Approved (brainstorm complete, pending implementation-plan)
**Author:** brainstorm session between user and Claude
**Scope:** New CLI subcommand `ollama-sentinel triage` that reads terminal
output (tracebacks, lint errors, failed tests) from stdin or a file and
produces a diagnose-and-fix suggestion via the local Ollama model. Inspired
by Phind's `Cmd+Shift+L` "ask about terminal output" feature. Reuses the
ContextBuilder infrastructure (`TokenCounter`, recipe pattern, `OllamaClient`).

---

## Goals

1. One CLI command that turns a pasted stack trace / test log into a
   diagnosis with a concrete fix suggestion, using the local Ollama model.
2. Automatic extraction of referenced source files from tool output
   (Python tracebacks, ruff, mypy, pytest) — zero flags needed for the
   common case.
3. Local-first, no network other than the existing local Ollama host.

## Non-goals

- Applying the suggested fix automatically. v1 prints the diagnosis +
  unified diff to stdout (or a file); applying it is the user's job.
- A live watch mode for test runs. `triage` is a one-shot command.
- Ticket / issue tracker integration. If the user wants to ship the triage
  somewhere, they use `--output` or pipe to another tool.
- Multi-step agentic back-and-forth with the model. Single request /
  response. More elaborate loops are a separate design.
- A VSCode extension wrapping `triage`. Terminal-only for now.
- Persistent history on disk. Triage is ephemeral; users who want history
  `| tee triage.md` or pass `--output`.

## Constraints

- Python >= 3.10 (matches existing project).
- Async-first where it matters: the Ollama call goes through the existing
  async `OllamaClient`. The CLI entrypoint runs an event loop internally.
- Zero new runtime dependencies. The project already ships `rich`, `typer`,
  `httpx`, `pathspec`, and after ContextBuilder, `tiktoken` and `diskcache`.
- Pydantic v2 for any new config schema (none planned — triage fits
  entirely within existing `OllamaConfig.models`).

---

## CLI surface

New Typer command in `ollama_sentinel/cli.py`:

```
$ ollama-sentinel triage [INPUT] [OPTIONS]

  Diagnose terminal output (tracebacks, lints, failed tests) using a local
  model. Reads from INPUT path, or stdin if no path given.

Arguments:
  INPUT                       Path to a log/output file. Omit to read stdin.

Options:
  -c, --config PATH           ollama-sentinel.yaml (default: ./ollama-sentinel.yaml)
  -m, --model TEXT            Model role (default: "triage"; auto-fallback to "default")
  -o, --output PATH           Save triage output to this file in addition to printing
  --context PATH              Additional source file to include (repeatable)
  --no-extract                Disable auto-extraction of referenced file paths
  -v, --verbose               Debug logging
```

### Behavior

- **Input source**: if `sys.stdin.isatty()` is `False`, read stdin; else
  the positional `INPUT` is required. Error with a helpful message if
  neither is available.
- **Rendering**: detect `console.is_terminal` — render output with
  `rich.markdown.Markdown` when interactive; emit plain text when piped.
  `--output` always writes the plain markdown (pipe-clean), regardless of
  TTY state.
- **Model role**: `-m triage` is the default. If config has no `triage`
  entry in `ollama.models`, the hybrid fallback kicks in: use the
  `default` role's model *name* but override its `system_prompt` with the
  built-in triage prompt. Log the fallback at `INFO` on first use per run.
- **Context**: auto-extracted references and `--context` files both feed
  the same recipe. `--no-extract` skips the auto-extraction branch, leaving
  only explicit `--context` inputs.
- **Exit codes**: 0 success; 1 input/config error; 2 model error after
  retries.

---

## Module layout

Two new files + one appended module + CLI wiring.

```
ollama_sentinel/
├── triage/
│   ├── __init__.py          # public: run_triage, extract_references, Reference
│   ├── extractor.py         # regex set + Reference dataclass; pure, no I/O
│   └── runner.py            # TriageRunner: input → extract → recipe → model → output
└── context/
    └── recipes.py           # + build_triage_context() appended here
```

`ollama_sentinel/cli.py` gains one `@app.command()` function (`triage`) that
imports and calls `run_triage(...)`.

### Why this layout

- `extractor.py` is a pure function. String in, `list[Reference]` out.
  Easy to unit-test against fixture logs without any filesystem or Ollama.
- `runner.py` is the integration seam. It handles reading files from disk,
  enforcing containment via `safe_read`, calling the recipe, talking to the
  Ollama client, and printing or saving output. Mirrors `FileProcessor` but
  leaner (no chunking, no violation DB, no versioned history).
- `build_triage_context` lives in `context/recipes.py` alongside the
  existing two recipes. Consistent pattern: terminal output plays the role
  of "query" for the assembler.
- The triage package (`ollama_sentinel/triage/`) is a package and not a
  single file because the extractor deserves its own tests directory and
  the runner has enough surface to be worth isolating.

---

## Reference extraction

`extractor.py` — a pure function with a small regex set. Returns deduped
references resolved against the caller's `cwd`.

### Data type

```python
@dataclass(frozen=True)
class Reference:
    path: str          # as it appeared in the input (may be relative)
    line: int | None   # 1-indexed line number, None if not parseable
    tool_hint: str     # "traceback" | "pytest" | "mypy" | "ruff" | "generic"
```

### Public function

```python
def extract_references(
    text: str, *, cwd: pathlib.Path | None = None
) -> list[Reference]: ...
```

### Regex set (v1, run in declared order; first match wins per line)

| Hint | Pattern | Example input |
|---|---|---|
| `traceback` | `File "([^"]+)", line (\d+)` | `File "ollama_sentinel/processor.py", line 42, in generate_review` |
| `pytest` | `^([^\s:]+\.py):(\d+)(?::\s|\s+in\s)` (multiline) | `tests/test_assembler.py:80: in test_empty_optional_section_is_dropped` |
| `mypy` | `^([^\s:]+):(\d+):(\d+):\s+error:` | `ollama_sentinel/processor.py:115:12: error: ...` |
| `ruff` | `^([^\s:]+):(\d+):(\d+):\s+[A-Z]\d+\b` | `ollama_sentinel/models.py:7:1: F401 ...` |
| `generic` | `\b([\w./-]+\.\w{1,5}):(\d+)\b` | any `path.ext:line` the above missed |

### Normalization

- Paths resolved against `cwd` (defaulting to `pathlib.Path.cwd()`).
- Paths passed through the same containment check as `safe_read`
  (`Path.relative_to` against `cwd`). Paths that escape are silently
  dropped with a `DEBUG` log line — never fetched.
- Non-existent paths dropped after resolve (common when logs reference
  files with a different working directory or deleted files).
- Duplicates collapsed by `(resolved_path, line)`. Same `file:42` twice
  → one entry; different lines in the same file → multiple entries.

### Bounds

- Hard cap of **50 references** returned. Massive logs don't blow up the
  token budget calculation; this is a pragmatic pre-filter before the
  assembler's budget math runs.
- Empty input → `[]`, no error.
- Known trade-off: the `generic` fallback will occasionally false-positive
  on inline strings that happen to look like paths (e.g.,
  `config: foo.yaml:23`). Acceptable noise — `safe_read` containment +
  file-existence filtering catches almost all of them.

---

## `build_triage_context` recipe

Appended to `ollama_sentinel/context/recipes.py`. Same shape as the other
two recipes — named sections with budget ratios, single call to `assemble()`.

### Signature

```python
async def build_triage_context(
    *,
    tool_output: str,
    references: Sequence[Reference],
    explicit_context_files: Sequence[pathlib.Path],
    counter: TokenCounter,
    total_budget: int,
    cwd: pathlib.Path,
) -> str:
    """Triage recipe — assembles tool output + referenced source excerpts."""
```

### Sections (in listed order)

1. **`TOOL OUTPUT`** — `Priority.MUST_FIT`, `soft_budget = int(total_budget * 0.35)`,
   `truncate="head"`. Single item: the raw tool output. Head-truncation
   keeps the error tail (the high-signal part) intact.

2. **`REFERENCED SOURCE`** — `Priority.OPTIONAL`,
   `soft_budget = int(total_budget * 0.45)`. One `ContextItem` per unique
   referenced file (not per reference). Items are pre-sorted by
   mention-frequency descending. No `Retriever` — the recipe does the
   ranking itself via `collections.Counter`.

   Each item is rendered as:

   ```
   -- <rel_path> (referenced at lines X, Y, Z) --
   ```<language>
   NNNN|<line content>
   NNNN|<line content>
   ...
   ```
   ```

   `<language>` is inferred from file suffix (`.py` → `py`, etc.).

   **Excerpt strategy:** for each file, compute
   `window_start = max(1, min_referenced_line - 8)` and
   `window_end = min(total_lines, max_referenced_line + 8)`.
   If `(window_end - window_start + 1) > 0.8 * total_lines`, render the
   whole file without line-number prefixes (saves tokens). Otherwise emit
   the windowed excerpt with 4-digit zero-padded line-number prefixes so
   the model can cite them back accurately.

3. **`USER-PROVIDED CONTEXT`** — `Priority.OPTIONAL`,
   `soft_budget = int(total_budget * 0.20)`. Rendered as whole files (no
   windowing — user explicitly asked for them). No retriever. Appears
   after auto-extracted sources so budget pressure drops user-provided
   first — principle: implicit signal beats manual addition when budgets
   conflict.

### `query` argument to `assemble()`

The `tool_output` itself. Currently no section uses a `Retriever`, so
`query` is unused in the critical path. Passing it anyway is free and
future-proofs the recipe for a later swap to `SemanticRetriever` on the
source section.

### Budget arithmetic

35 + 45 + 20 = 100. Must-fit reserves 35%, optional tiers share the
remaining 65%. The assembler's existing rules handle under-budget
gracefully (sections drop in reverse declared order).

### Language-suffix map

A small dict at module level in `recipes.py`:

```python
_LANG_FENCE = {
    ".py": "py", ".rb": "rb", ".ts": "ts", ".tsx": "tsx", ".js": "js",
    ".jsx": "jsx", ".go": "go", ".rs": "rs", ".java": "java",
    ".c": "c", ".cpp": "cpp", ".h": "c", ".sh": "bash", ".yaml": "yaml",
    ".yml": "yaml", ".json": "json", ".toml": "toml", ".md": "markdown",
}
```

Unknown suffixes fall back to an empty fence (triple backticks only).

---

## Built-in triage system prompt

Used when config has no `triage` role, via the hybrid fallback in the
runner. Lives as a module-level constant in `triage/runner.py`.

```
You are a senior developer helping triage a failing build or test run.
Given the tool output and referenced source code, respond with:

1. DIAGNOSIS: one-sentence root cause (be specific — name the variable,
   function, or assertion).
2. FIX: the concrete change. Include a unified diff when possible.
3. CONFIDENCE: low / medium / high, based on whether the provided
   source is sufficient to be sure.

If the source isn't enough, say so and name what else you'd need.
Do not speculate beyond the evidence.
```

Users can override by adding a `triage` entry to `ollama.models` in their
YAML. `ollama-sentinel init` will emit a `triage` role with this prompt
starting from the first implementation commit that touches `config.py`
defaults (see "Config change" below).

---

## `TriageRunner` orchestration

`triage/runner.py` — the integration seam. Public callable:

```python
async def run_triage(
    *,
    input_text: str,
    config: SentinelConfig,
    cwd: pathlib.Path,
    model_role: str = "triage",
    explicit_context: Sequence[pathlib.Path] = (),
    extract: bool = True,
) -> str:
    """Return the rendered triage markdown. Caller handles printing / saving."""
```

### Flow

1. If `extract`, call `extract_references(input_text, cwd=cwd)`; else use
   an empty list.
2. Compute `total_budget` from `config.ollama.models["default"].context_window
   - config.ollama.models["default"].output_reserve_tokens`. (Always keyed
   by the literal `"default"` role — the triage model, if any, is expected
   to be comparably sized. Same trick as `FileProcessor`.)
3. `TokenCounter()` → inject into the recipe.
4. `prompt = await build_triage_context(...)`.
5. Resolve the active model role:
   - Look up `config.ollama.models[model_role]`.
   - **If `model_role == "triage"` and missing**: hybrid fallback kicks in.
     Log `INFO` "triage role not configured; using default model with
     built-in prompt", then synthesize an `OllamaModelConfig` at runtime
     by copying `config.ollama.models["default"]` and replacing its
     `system_prompt` with the built-in `TRIAGE_SYSTEM_PROMPT`.
   - **If `model_role` is anything else (user passed `-m custom`) and
     missing**: error and exit 1. The fallback is intentional only for the
     default `triage` role; other roles must exist in config.
6. Call `OllamaClient.generate_review(model_role, prompt)` — the name is
   legacy but the method is generic "send prompt, return content."
7. Return the model's text.

### Why a custom `OllamaClient` call for fallback

`OllamaClient.generate_review(role, prompt)` looks up `role` in `self.config["models"]`. For the hybrid fallback, the synthesized triage config isn't in that dict — we need one of:

- **(a)** Mutate `ollama_client.config["models"]["triage"]` at the runner's entry (ephemeral, gets reset next construction).
- **(b)** Add a method to `OllamaClient` that takes an explicit `OllamaModelConfig` instead of a role name.

Design decision: **(b)**. Add `OllamaClient.generate_with_model(model_config: OllamaModelConfig | dict, prompt: str) -> str`. The existing `generate_review` becomes a thin wrapper that looks up the role and delegates. Cleaner contract, no hidden mutation, testable.

### CLI wiring

`cli.py` gets a new command that:
1. Reads stdin or `INPUT`.
2. Loads config.
3. Resolves explicit `--context` paths to absolute Paths, verifies they exist.
4. Calls `asyncio.run(run_triage(...))`.
5. Renders output via `rich.markdown.Markdown` if `console.is_terminal` else plain `print`.
6. If `--output` set, writes plain text to the file.

---

## Config change

`create_default_config` in `ollama_sentinel/config.py` gains a `triage`
entry under `ollama.models`:

```python
"models": {
    "default": { ... existing ... },
    "triage": {
        "name": "gemma3:4b",
        "system_prompt": TRIAGE_SYSTEM_PROMPT,   # same text imported from runner
        "context_window": 8192,
        "output_reserve_tokens": 2000,
    },
}
```

The existing default already emits a `default` model with the same
`gemma3:4b`. The triage model name starts identical; users can tune it
later without touching code.

Existing YAML files without a `triage` entry are unaffected — the hybrid
fallback handles them.

---

## Error handling

| Failure | Behavior |
|---|---|
| `sys.stdin.isatty()` True and no positional path | Typer error: `"No input — pipe tool output or pass a path."` Exit 1. |
| `INPUT` path unreadable (missing / permission / symlink outside cwd) | Error: `"Cannot read <path>: <reason>"`. Exit 1. |
| Input is empty or whitespace-only | Warn at `INFO`; exit 0 without calling the model. |
| Extractor finds zero references | Proceed with `TOOL OUTPUT` + `USER-PROVIDED CONTEXT` only; log `INFO` "No file references auto-extracted". |
| Auto-extracted path escapes cwd | Drop silently; `DEBUG` log. Same containment as `safe_read`. |
| Auto-extracted path does not exist on disk | Drop silently; `DEBUG` log with the count of dropped paths. |
| `--context` file unreadable | Error and exit 1. Explicit user intent — don't swallow. |
| Config missing `triage` role | Hybrid fallback: default model name + built-in triage prompt. Log `INFO` on first use. |
| Config file missing entirely | Error and exit 1 with hint to run `init`. Same as `review`. |
| Ollama HTTP error or timeout | Existing `tenacity` retry in `OllamaClient` applies. After retries fail, print friendly message and exit 2. No partial output saved. |
| Prompt over total budget after assembly | Handled by `assemble()` (already tested). `TOOL OUTPUT` uses `truncate="head"`, keeping the error tail. |
| `--output` path unwritable | Error and exit 1 *after* printing to stdout. User still sees the triage. |
| Terminal encoding can't render model output | Rich handles most cases; fall back to ASCII-safe `print(output.encode('ascii', 'replace').decode())` if `Console.print` raises. |

**Guiding principle:** degrade to "you still get a triage, possibly with
less context." Stdin failures are the one exception — without input, there
is nothing to do.

---

## Verbose mode (`-v`)

Enables `DEBUG` logging globally. Users see:

- Which tool hint matched for each reference.
- Counts of paths dropped (traversal, missing, dedup).
- The full assembled prompt before sending (for tuning the system prompt
  during development).
- Ollama request / retry details.

---

## Testing

Same conventions as the rest of the project — pytest with
`asyncio_mode = "auto"`, `pytest-httpx` for Ollama, no live processes,
fixtures in `tests/conftest.py` where reusable.

### New test files

1. **`tests/triage/__init__.py`** — empty.

2. **`tests/triage/test_extractor.py`** — table-driven with fixture logs
   checked into `tests/triage/fixtures/`. Covers:
   - Python traceback with nested frames.
   - Pytest failure summary (`FAILED tests/x.py::test_y - AssertionError`).
   - Ruff output (`src/foo.py:5:1: F401 ...`).
   - Mypy output (`src/foo.py:12:8: error: ...`).
   - Mixed input (one log with multiple tools).
   - Generic `path:line` fallback where specific matchers miss.
   - Dedup of the same `file:line` mentioned twice.
   - Path-traversal attempt (`../../etc/passwd:1`) dropped.
   - Non-existent paths dropped after resolve.
   - 50-reference hard cap enforced.
   - Empty input → `[]`.

3. **`tests/triage/test_runner.py`** — `TriageRunner` integration. Uses
   `tmp_path` + `pytest-httpx`. Covers:
   - Happy path: stdin input, one referenced file, model returns markdown.
   - `--context` flag: explicit file added to the prompt.
   - `--no-extract`: auto-extraction disabled, only `--context` files in.
   - `--output`: file written with the plain markdown body.
   - Missing config → exit 1.
   - Missing `triage` role → hybrid fallback; built-in system prompt sent
     to Ollama (assertable by inspecting the httpx request body).
   - Ollama HTTP 500 → exit 2 after retries.
   - Empty stdin → no model call, exit 0.

### Extended tests

4. **`tests/context/test_recipes.py`** — append `TestBuildTriageContext`,
   ~5 cases:
   - `TOOL OUTPUT` present and uses `truncate="head"` when overflowing.
   - Referenced sources emitted in mention-frequency order (most-referenced
     first).
   - Excerpt windowing clamps correctly at file start and file end.
   - Whole-file rendering kicks in when the window covers >80% of the file.
   - User-provided `--context` files appear after auto-extracted ones.

5. **`tests/test_cli.py`** — append `TestTriageCommand`. Uses Typer's
   `CliRunner`:
   - `ollama-sentinel triage --help` renders.
   - Piped stdin consumed.
   - TTY mode without input exits with helpful error.
   - `--output` writes the expected file.

### Targets

- ~25 new tests.
- Full suite stays under 3 seconds (all HTTP mocked, no network).
- Coverage: extractor regex paths + runner error-handling matrix both hit.

### Out of test scope

- Live Ollama integration. Manual smoke step in the plan, not CI.
- Quality of the built-in system prompt. That's an eval concern, separate
  from correctness testing.

---

## Decided side-points

- **Rank-by-frequency, not semantic.** Tracebacks are already ranked by
  the tool that produced them — files mentioned more often matter more.
  `SemanticRetriever` would burn Ollama embeddings round-trips on a hot
  path without meaningful gain. Easy to swap in later if evidence emerges
  otherwise.
- **Head-truncation on `TOOL OUTPUT`.** Non-default, but correct: error
  tails carry more signal than preamble. Worth the one-line deviation.
- **No persistent history directory.** Triage is ephemeral. Users who
  want history `| tee` or `--output` on demand. Keeps the mental model
  simple: no silent filesystem state, no cleanup task.

## Not in scope for this spec

- Interactive "apply this diff" mode. If the model returns a unified diff,
  the user runs `patch` themselves.
- Multi-turn dialogue ("I tried your suggestion, still broken").
- Caching of triages keyed by input hash. Every invocation hits the model.
- CI integration (e.g., auto-triage failing jobs). Users can wire that up
  outside the tool.
- A `--json` output mode. If a second consumer needs machine-readable
  output, we'll revisit; not worth designing for today.
- Extracting references from non-textual logs (binary, gzipped). User
  decompresses before piping.
