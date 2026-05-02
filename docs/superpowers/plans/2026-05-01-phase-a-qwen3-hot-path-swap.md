# Phase A: Qwen3 hot-path swap + pre-registered role schema

**Status:** ready to ship
**Effort:** ~3-4 hours implementation + ~1 hour smoke testing
**Owner:** unassigned (different agent team than the one implementing CB-3)
**Plan source:** Phase A of `~/.claude/plans/yes-putting-both-moonlit-galaxy.md`,
scoped down per session decisions in May 2026

---

## Why this exists, in one paragraph

Today `EmbeddingConfig` is a flat shape — `enabled: bool` and `model: str` —
with `nomic-embed-text` as the default. This ticket refactors it into a
named-role dictionary mirroring `OllamaConfig.models`, swaps the hot-path
default to `qwen3-embedding:4b`, and **pre-registers** the `consolidation`
and `rerank` role keys in the schema even though no consumer is wired for
them yet. Pre-registration locks the role naming convention before future
phases, costs ~zero extra schema work today, and removes a future migration.
Legacy `model: foo` YAML auto-migrates with a deprecation warning that
explicitly threatens hard-error in v0.3.

This ticket does **not** add a reranker, does **not** wire `consolidation`
into any consumer, does **not** introduce `report --quality high`, does
**not** pull `qwen3-embedding:8b` or any reranker model. The `rerank` role
key is registered with `None` as the default model name — explicitly
unassigned.

The user is approaching this work with a "ship visible motion this weekend"
goal. The hot-path swap to a stronger embedder produces noticeably better
recall on real codebases. A before/after diff capture script is included in
this spec — running it is part of the acceptance criteria, not optional.

---

## Acceptance criteria

1. **Schema refactor.** `EmbeddingConfig` uses
   `models: Dict[str, Optional[str]]` with default
   `{"hot": "qwen3-embedding:4b", "consolidation": "qwen3-embedding:8b", "rerank": None}`.
2. **Strict schema policy.** `EmbeddingConfig` declares
   `model_config = ConfigDict(extra="forbid")` so unknown fields raise.
3. **Legacy migration.** `model_validator(mode="before")` accepts a top-level
   `model: str` field, lifts it to `models["hot"]`, emits a deprecation
   warning that explicitly says the legacy field will hard-error in v0.3,
   and refuses to allow both `model` and `models` to coexist.
4. **Validator.** `field_validator("models")` requires `"hot"` to be present,
   requires `models["hot"]` to be a non-empty string, and allows other roles
   to be either a non-empty string or `None`.
5. **Hot-path consumer updated.** `FileProcessor` reads
   `config.embedding.models["hot"]` instead of `config.embedding.model`.
   No other consumer is wired for now.
6. **OllamaEmbedder default.** The default `model` constructor argument flips
   from `"nomic-embed-text"` to `"qwen3-embedding:4b"`. Cache-key namespacing
   is unchanged (`embed:{model}:{key}`), so old vectors don't collide.
7. **Default config writer.** `create_default_config()` writes the new
   `embedding.models` block, including the unassigned `rerank` role.
8. **Repo example YAML.** `ollama-sentinel.yaml` at the repo root gains an
   explicit `embedding:` block with all three roles.
9. **Tests.** All listed test additions and updates pass; full suite stays
   green. Expect approximately 8-10 new tests on top of current count.
10. **Smoke verification.** Run the before/after recall diff capture script
    (provided below) on a real project. Save the diff to
    `docs/superpowers/notes/qwen3-recall-diff.md` and reference it in the PR.
11. **Docs.** README, CLAUDE.md, and `docs/GUIDE.md` updated to reflect the
    new model pull. CLAUDE.md "Recent landings" gets a new entry. CLAUDE.md
    "Pickable next moves" stays unchanged (Phases B and C are still parked).

**Out of scope:**
- Wiring `consolidation` into any consumer. Schema only.
- Wiring `rerank` into any consumer. Schema only.
- `report --quality high` flag. That's Phase B, parked.
- `OllamaReranker` class. That's Phase C, parked.
- Pulling `qwen3-embedding:8b` or any reranker model. Schema only.
- Touching `research_agent/core/workflow.py`'s embedder construction
  (`embed_cfg.get("model", "nomic-embed-text")`). The dict-config path on
  `research_agent` is **not** part of Phase A. CB-3 already runs against
  the legacy shape; deferring research_agent's migration keeps the blast
  radius bounded. A future ticket migrates research_agent.

---

## Current state — verified by reading the repo on this branch

Ground truth as of writing:

- `ollama_sentinel/models.py:131-134` — `EmbeddingConfig` is a flat 2-field
  Pydantic model (`enabled`, `model`). No `model_config`. No validators.
- `ollama_sentinel/config.py:124-127` — `create_default_config()` writes
  `"embedding": {"enabled": True, "model": "nomic-embed-text"}`.
- `ollama_sentinel/context/embeddings.py:34` — `OllamaEmbedder.__init__`
  takes `model: str = "nomic-embed-text"` as a default.
- `ollama_sentinel/processor.py` — reads `config.embedding.model` when
  constructing the embedder. Search for `config.embedding.model` to find
  the call site; one occurrence.
- `ollama-sentinel.yaml` — has **no** `embedding:` block. Defaults silently
  apply. (Spec adds the block.)
- `tests/test_models.py:202-205` — `TestEmbeddingConfig.test_defaults`
  asserts `cfg.model == "nomic-embed-text"`. Update.
- `tests/test_config.py:91-93` — `test_emits_embedding_section` asserts
  `config["embedding"]["model"] == "nomic-embed-text"`. Update.
- `tests/conftest.py:43-83` — `config_yaml_path` fixture writes a YAML with
  no `embedding:` block. Defaults apply silently. Spec leaves the fixture
  alone; new legacy-migration test inlines its own YAML.

If any of the above has changed by the time this ticket is picked up,
re-confirm before applying the diff.

---

## Implementation — exact diff

The implementation order matches a strict TDD shape: write each failing
test, run, confirm fail, implement, run, confirm pass, commit. Do **not**
batch commits. Each numbered step is one commit.

### Step 1 — Update `EmbeddingConfig` baseline test to expect new shape

File: `tests/test_models.py`

Locate the existing block (around line 200):

```python
class TestEmbeddingConfig:
    def test_defaults(self):
        cfg = EmbeddingConfig()
        assert cfg.enabled is True
        assert cfg.model == "nomic-embed-text"
```

Replace with:

```python
class TestEmbeddingConfig:
    def test_defaults(self):
        cfg = EmbeddingConfig()
        assert cfg.enabled is True
        # See TestEmbeddingConfigMigration for full models-shape coverage.
        assert cfg.models["hot"] == "qwen3-embedding:4b"
```

Then append a new test class to the file:

```python
class TestEmbeddingConfigMigration:
    """Phase A: EmbeddingConfig is a named-role dict with legacy migration."""

    def test_default_models_shape(self):
        cfg = EmbeddingConfig()
        assert set(cfg.models.keys()) == {"hot", "consolidation", "rerank"}
        assert cfg.models["hot"] == "qwen3-embedding:4b"
        assert cfg.models["consolidation"] == "qwen3-embedding:8b"
        assert cfg.models["rerank"] is None

    def test_models_must_include_hot_role(self):
        with pytest.raises(ValidationError, match="hot"):
            EmbeddingConfig(models={"consolidation": "x"})

    def test_hot_role_must_be_non_empty_string(self):
        with pytest.raises(ValidationError, match="non-empty"):
            EmbeddingConfig(models={"hot": "  "})

    def test_other_roles_may_be_none(self):
        cfg = EmbeddingConfig(models={"hot": "h", "rerank": None})
        assert cfg.models["rerank"] is None

    def test_other_roles_must_be_non_empty_string_when_set(self):
        with pytest.raises(ValidationError, match="non-empty"):
            EmbeddingConfig(models={"hot": "h", "consolidation": "  "})

    def test_extra_top_level_field_is_forbidden(self):
        # Phase A locks the schema with extra="forbid" so typos in YAML
        # surface loudly rather than silently being ignored.
        with pytest.raises(ValidationError):
            EmbeddingConfig(enabled=True, models={"hot": "h"}, oops=True)

    def test_legacy_model_field_migrates_to_models_hot(self, caplog):
        with caplog.at_level("WARNING"):
            cfg = EmbeddingConfig(model="legacy-embed-name")
        assert cfg.models["hot"] == "legacy-embed-name"
        # Other roles still get their schema defaults — pre-registration
        # is a property of the schema, not of the user-supplied dict.
        assert cfg.models["consolidation"] == "qwen3-embedding:8b"
        assert cfg.models["rerank"] is None
        # The warning must explicitly threaten the v0.3 hard-error.
        assert "v0.3" in caplog.text or "0.3" in caplog.text
        assert "deprecated" in caplog.text.lower()

    def test_both_model_and_models_rejected(self):
        with pytest.raises(ValidationError, match="mutually exclusive"):
            EmbeddingConfig(model="x", models={"hot": "y"})
```

Run:

```bash
pytest tests/test_models.py::TestEmbeddingConfigMigration -v
pytest tests/test_models.py::TestEmbeddingConfig::test_defaults -v
```

Expected: **all fail**. Commit:

```
git add tests/test_models.py
git commit -m "test(models): failing tests for EmbeddingConfig pre-registered roles + legacy migration"
```

### Step 2 — Implement `EmbeddingConfig` shape

File: `ollama_sentinel/models.py`

Locate (around line 131):

```python
class EmbeddingConfig(BaseModel):
    """Configuration for the Ollama embedding backend."""
    enabled: bool = True
    model: str = "nomic-embed-text"
```

Replace with:

```python
class EmbeddingConfig(BaseModel):
    """Configuration for the Ollama embedding backend.

    `models` is a name->model-id map. The `hot` role is required and is
    used on every file save. `consolidation` and `rerank` are pre-registered
    in the schema but unwired today — they exist so future work doesn't
    need a second config migration. `rerank` defaults to None because the
    canonical reranker model is not yet chosen; pick it when Phase C lands.

    The legacy flat-`model` field auto-migrates with a deprecation warning.
    The legacy field WILL HARD-ERROR in v0.3 — fix configs now.
    """
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    models: Dict[str, Optional[str]] = {
        "hot": "qwen3-embedding:4b",
        "consolidation": "qwen3-embedding:8b",
        "rerank": None,
    }

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy_model_field(cls, data):
        if not isinstance(data, dict):
            return data
        has_legacy = "model" in data
        has_new = "models" in data
        if has_legacy and has_new:
            raise ValueError(
                "embedding.model and embedding.models are mutually exclusive; "
                "remove the legacy 'model' field."
            )
        if has_legacy:
            log.warning(
                "embedding.model is deprecated and will hard-error in v0.3; "
                "auto-migrating to embedding.models.hot for now."
            )
            # Preserve schema defaults for non-hot roles by merging into the
            # default dict rather than replacing it.
            migrated = {
                "hot": data["model"],
                "consolidation": "qwen3-embedding:8b",
                "rerank": None,
            }
            data = {k: v for k, v in data.items() if k != "model"}
            data["models"] = migrated
        return data

    @field_validator("models")
    @classmethod
    def _validate_models(cls, v: Dict[str, Optional[str]]) -> Dict[str, Optional[str]]:
        if "hot" not in v:
            raise ValueError("embedding.models must include a 'hot' role")
        hot = v["hot"]
        if not isinstance(hot, str) or not hot.strip():
            raise ValueError("embedding.models['hot'] must be a non-empty string")
        for role, name in v.items():
            if name is None:
                # None means "role registered, no model assigned." Allowed
                # for any role except hot (handled above).
                continue
            if not isinstance(name, str) or not name.strip():
                raise ValueError(
                    f"embedding.models[{role!r}] must be a non-empty string or None"
                )
        return v
```

Add `ConfigDict` to the existing pydantic import at the top of `models.py`:

```python
# Before:
from pydantic import BaseModel, field_validator, model_validator
# After:
from pydantic import BaseModel, ConfigDict, field_validator, model_validator
```

Run:

```bash
pytest tests/test_models.py::TestEmbeddingConfigMigration tests/test_models.py::TestEmbeddingConfig -v
pytest tests/test_models.py -v
```

Expected: **all pass**. Commit:

```
git add ollama_sentinel/models.py
git commit -m "feat(models): EmbeddingConfig.models pre-registers hot/consolidation/rerank roles

Default flips to {hot: qwen3-embedding:4b, consolidation: qwen3-embedding:8b,
rerank: None}. Legacy 'model: foo' YAML auto-migrates with a deprecation
warning that threatens hard-error in v0.3. Schema is sealed with
extra='forbid' so typos surface loudly."
```

### Step 3 — Update `OllamaEmbedder` constructor default

File: `ollama_sentinel/context/embeddings.py`

Locate the test file first to update assertions before changing the
implementation. In `tests/context/test_embeddings.py`, find every assertion
that references `nomic-embed-text` (likely cache-key strings around lines
37, 41, 56). Replace each `nomic-embed-text` literal with `qwen3-embedding:4b`.

If a test exists that constructs `OllamaEmbedder(host=...)` without
`model=...` and asserts the model property, update its expected value.

Run the failing tests:

```bash
pytest tests/context/test_embeddings.py -v
```

Expected: **fail** on the assertions you just updated.

Then change the default in `ollama_sentinel/context/embeddings.py:34`:

```python
# Before:
model: str = "nomic-embed-text",
# After:
model: str = "qwen3-embedding:4b",
```

Run:

```bash
pytest tests/context/test_embeddings.py -v
```

Expected: **all pass**. Commit:

```
git add ollama_sentinel/context/embeddings.py tests/context/test_embeddings.py
git commit -m "feat(embeddings): OllamaEmbedder defaults to qwen3-embedding:4b"
```

### Step 4 — Update `FileProcessor` to read `models["hot"]`

File: `ollama_sentinel/processor.py`

Locate the embedder instantiation. The line currently reads:

```python
self.embedder = OllamaEmbedder(
    host=config.ollama.host,
    model=config.embedding.model,
    cache=self._cache,
)
```

(There is exactly one such call site. If your search reveals more than one,
stop and re-confirm against the spec — something has drifted.)

Replace with:

```python
self.embedder = OllamaEmbedder(
    host=config.ollama.host,
    model=config.embedding.models["hot"],
    cache=self._cache,
)
```

The `field_validator("models")` guarantees `"hot"` is present and non-empty,
so `models["hot"]` cannot raise `KeyError` for a validated config.

Run:

```bash
pytest tests/test_processor.py -v
```

Expected: **all pass**. If anything fails, the most likely cause is a test
that mocked `config.embedding.model`; update those mocks to use
`config.embedding.models = {"hot": "..."}` instead.

Commit:

```
git add ollama_sentinel/processor.py
git commit -m "feat(processor): read embedding model from config.embedding.models[hot]"
```

### Step 5 — Update `create_default_config()` and its tests

File: `ollama_sentinel/config.py`

First, update the test in `tests/test_config.py`. Locate
`test_emits_embedding_section` (around line 91):

```python
def test_emits_embedding_section(self):
    config = create_default_config(".")
    assert "embedding" in config
    assert config["embedding"]["model"] == "nomic-embed-text"
    assert config["embedding"]["enabled"] is True
```

Replace with:

```python
def test_emits_embedding_section(self):
    config = create_default_config(".")
    assert "embedding" in config
    assert config["embedding"]["enabled"] is True
    assert config["embedding"]["models"]["hot"] == "qwen3-embedding:4b"
    assert config["embedding"]["models"]["consolidation"] == "qwen3-embedding:8b"
    assert config["embedding"]["models"]["rerank"] is None
```

Then add a new test in the same class:

```python
def test_legacy_yaml_model_migrates_on_load(self, tmp_path):
    """A user YAML with embedding.model loads and lifts to models.hot."""
    config_dict = {
        "watch": {"directory": str(tmp_path)},
        "ollama": {
            "host": "http://localhost:11434",
            "models": {"default": {"name": "m", "system_prompt": "p"}},
        },
        "embedding": {
            "enabled": True,
            "model": "legacy-embed-name",  # legacy flat shape
        },
    }
    cfg_path = tmp_path / "ollama-sentinel.yaml"
    cfg_path.write_text(yaml.dump(config_dict))
    cfg = load_config(cfg_path)
    assert cfg is not None
    assert cfg.embedding.models["hot"] == "legacy-embed-name"
    # Schema defaults still apply to other roles after migration.
    assert cfg.embedding.models["consolidation"] == "qwen3-embedding:8b"
    assert cfg.embedding.models["rerank"] is None
```

Run:

```bash
pytest tests/test_config.py -v
```

Expected: **fail**. Then update `ollama_sentinel/config.py:124-127`:

```python
# Before:
"embedding": {
    "enabled": True,
    "model": "nomic-embed-text",
},
# After:
"embedding": {
    "enabled": True,
    "models": {
        "hot": "qwen3-embedding:4b",
        "consolidation": "qwen3-embedding:8b",
        "rerank": None,
    },
},
```

Run again:

```bash
pytest tests/test_config.py -v
```

Expected: **pass**. Commit:

```
git add ollama_sentinel/config.py tests/test_config.py
git commit -m "feat(config): default starter config writes pre-registered embedding roles"
```

### Step 6 — Add `embedding:` block to repo example YAML

File: `ollama-sentinel.yaml`

The file has no `embedding:` block today. After the `memory:` block (or
wherever fits the existing ordering), add:

```yaml
embedding:
  enabled: true
  models:
    hot: qwen3-embedding:4b
    # The next two roles are pre-registered in the schema but UNWIRED.
    # No consumer reads them today. They're here so future phases don't
    # need another config migration. Leaving them at these defaults is fine;
    # rerank: null means "role exists, no model assigned yet."
    consolidation: qwen3-embedding:8b
    rerank: null
```

Sanity check the YAML loads and matches the schema:

```bash
python -c "
from ollama_sentinel.config import load_config
import pathlib
cfg = load_config(pathlib.Path('ollama-sentinel.yaml'))
assert cfg is not None
print('hot          :', cfg.embedding.models['hot'])
print('consolidation:', cfg.embedding.models['consolidation'])
print('rerank       :', cfg.embedding.models['rerank'])
"
```

Expected output:

```
hot          : qwen3-embedding:4b
consolidation: qwen3-embedding:8b
rerank       : None
```

Commit:

```
git add ollama-sentinel.yaml
git commit -m "chore(config): repo example YAML registers all three embedding roles"
```

### Step 7 — Update docs

Files: `CLAUDE.md`, `README.md`, `docs/GUIDE.md`

Find every mention of `nomic-embed-text`:

```bash
grep -rn "nomic-embed-text" CLAUDE.md README.md docs/
```

Replace each `ollama pull nomic-embed-text` with `ollama pull qwen3-embedding:4b`
(approx 2.5 GB vs nomic's ~280 MB — note the size shift where docs mention
storage cost).

In `CLAUDE.md` "Persistent gotchas," replace:

```markdown
- `ollama-sentinel run` requires `ollama pull nomic-embed-text` once on
  first use, or set `memory.semantic_recall: false` to fall back to the
  legacy exact-path recall.
```

with:

```markdown
- `ollama-sentinel run` requires `ollama pull qwen3-embedding:4b` once on
  first use (~2.5 GB), or set `memory.semantic_recall: false` to fall back
  to the legacy exact-path recall.
- `embedding.models.consolidation` and `embedding.models.rerank` are
  pre-registered in the schema but UNWIRED. Do NOT pull `qwen3-embedding:8b`
  or any reranker model unless you're picking up Phase B or C — they sit
  in the YAML so future phases don't need another config migration.
```

In `CLAUDE.md` "Recent landings," prepend:

```markdown
- YYYY-MM-DD: Phase A landed. Hot-path embedder swapped from
  nomic-embed-text to qwen3-embedding:4b. EmbeddingConfig refactored to a
  named-role dict; consolidation and rerank roles pre-registered but
  unwired. Legacy `embedding.model: foo` YAML auto-migrates with a
  deprecation warning that threatens hard-error in v0.3. Plan source:
  `~/.claude/plans/yes-putting-both-moonlit-galaxy.md` (Phase A only;
  B and C remain parked).
```

Commit:

```
git add CLAUDE.md README.md docs/GUIDE.md
git commit -m "docs: switch install instructions to qwen3-embedding:4b; document parked roles"
```

### Step 8 — Smoke test against live Ollama + capture before/after diff

> This step is mandatory. The "watching it move" feeling is the explicit
> goal of Phase A; the diff is the receipt.

Pull the model:

```bash
ollama pull qwen3-embedding:4b
ollama list | grep qwen3-embedding
```

Smoke-test the embedder:

```bash
python -c "
import asyncio
from ollama_sentinel.context.embeddings import OllamaEmbedder

async def main():
    e = OllamaEmbedder(host='http://localhost:11434')
    v = await e.embed('hello world')
    print(f'model={e.model} dim={len(v)} first3={v[:3]}')
    await e.close()

asyncio.run(main())
"
```

Expected: prints `model=qwen3-embedding:4b dim=2560 first3=[...]` (or
similar). Dimension may differ; the pipeline is dimension-agnostic.

**Before/after recall diff capture.** Create the script at
`scripts/embedding_recall_diff.py` (the script lives in the repo because
this exact diff has documentary value beyond Phase A — the `Incident` work
will reuse this pattern). Script body:

```python
"""Capture before/after semantic-recall diffs for a real codebase.

Usage:
    python scripts/embedding_recall_diff.py before
    # ... swap embedder model ...
    python scripts/embedding_recall_diff.py after
    python scripts/embedding_recall_diff.py compare

Writes:
    /tmp/recall_diff_before.json
    /tmp/recall_diff_after.json
    docs/superpowers/notes/qwen3-recall-diff.md  (compare step)

Picks 10 representative files from the watched directory and dumps the
top-5 semantic neighbors for each, using whatever embedder + violation DB
the current config selects. Run `before` against the OLD config, swap
the model, run `after` against the NEW config, then `compare`.
"""
from __future__ import annotations
import asyncio
import json
import pathlib
import sys
from typing import List

from ollama_sentinel.config import load_config
from ollama_sentinel.context.embeddings import OllamaEmbedder
from ollama_sentinel.violation_db import ViolationDB

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
TARGET_FILES: List[str] = [
    # Pick 10 files that have multiple unresolved findings in the existing
    # ViolationDB. The exact list depends on the project; default below
    # picks files most likely to have diverse findings in this repo.
    "ollama_sentinel/processor.py",
    "ollama_sentinel/watcher.py",
    "ollama_sentinel/violation_db.py",
    "ollama_sentinel/cli.py",
    "ollama_sentinel/extractor.py",
    "ollama_sentinel/dashboard.py",
    "ollama_sentinel/context/recipes.py",
    "ollama_sentinel/context/assembler.py",
    "research_agent/core/workflow.py",
    "research_agent/tools/memory.py",
]


async def capture(out_path: pathlib.Path) -> None:
    cfg = load_config(REPO_ROOT / "ollama-sentinel.yaml")
    if cfg is None:
        sys.exit("config did not load; check ollama-sentinel.yaml")
    embedder = OllamaEmbedder(
        host=cfg.ollama.host,
        model=cfg.embedding.models["hot"],
    )
    db = ViolationDB(str(REPO_ROOT / cfg.memory.db_path))
    try:
        report = {"model": embedder.model, "files": {}}
        for rel in TARGET_FILES:
            path = REPO_ROOT / rel
            if not path.exists():
                continue
            text = path.read_text(errors="replace")
            try:
                neighbors = await db.get_neighbors_by_similarity(
                    query_text=text, embedder=embedder, k=5,
                )
            except Exception as e:
                report["files"][rel] = {"error": str(e), "neighbors": []}
                continue
            report["files"][rel] = {
                "neighbors": [
                    {
                        "file_path": n["file_path"],
                        "lines": f"{n['line_start']}-{n['line_end']}",
                        "category": n["category"],
                        "severity": n["severity"],
                        "description": n["description"][:80],
                    }
                    for n in neighbors
                ]
            }
        out_path.write_text(json.dumps(report, indent=2))
        print(f"wrote {out_path}")
    finally:
        db.close()
        await embedder.close()


def compare() -> None:
    before = json.loads(pathlib.Path("/tmp/recall_diff_before.json").read_text())
    after = json.loads(pathlib.Path("/tmp/recall_diff_after.json").read_text())
    out = REPO_ROOT / "docs/superpowers/notes/qwen3-recall-diff.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Qwen3 recall diff",
        "",
        f"Before: `{before['model']}`",
        f"After:  `{after['model']}`",
        "",
    ]
    for rel in before["files"].keys():
        lines.append(f"## `{rel}`")
        lines.append("")
        lines.append("**Before**")
        for n in before["files"][rel].get("neighbors", []):
            lines.append(f"- {n['file_path']}:{n['lines']} [{n['severity']}] {n['description']}")
        lines.append("")
        lines.append("**After**")
        for n in after["files"][rel].get("neighbors", []):
            lines.append(f"- {n['file_path']}:{n['lines']} [{n['severity']}] {n['description']}")
        lines.append("")
    out.write_text("\n".join(lines))
    print(f"wrote {out}")


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in ("before", "after", "compare"):
        sys.exit("usage: embedding_recall_diff.py {before|after|compare}")
    mode = sys.argv[1]
    if mode == "compare":
        compare()
    else:
        out = pathlib.Path(f"/tmp/recall_diff_{mode}.json")
        asyncio.run(capture(out))
```

Workflow:

1. Stash the current Phase A diff: `git stash`. (You should be on a clean
   pre-Phase-A tree.)
2. Run `python scripts/embedding_recall_diff.py before`.
3. `git stash pop` — restore the Phase A changes.
4. Run `python scripts/embedding_recall_diff.py after`.
5. Run `python scripts/embedding_recall_diff.py compare`.
6. Open `docs/superpowers/notes/qwen3-recall-diff.md`. Skim. If most files
   show clearly different (and arguably better) neighbors after the swap,
   the work is real. If neighbors are identical or worse, **stop and report
   the finding in the PR before merging** — that's a signal something is
   wrong with the swap or the violation DB doesn't have enough diverse
   findings to show a difference.

Commit the diff capture:

```bash
git add scripts/embedding_recall_diff.py docs/superpowers/notes/qwen3-recall-diff.md
git commit -m "docs: add embedding recall diff capture script + Phase A receipt"
```

### Step 9 — Final regression sweep

```bash
pytest tests/ -v
```

Expected: full suite green, ~10 new tests beyond the prior count.

If `embedding.model` (legacy) is referenced anywhere not yet touched
(`grep -rn "embedding.model" --include="*.py" --include="*.yaml"`), fix
each and add a `fix(...): replace remaining embedding.model reference in
<file>` commit per fix. Don't bundle.

---

## Verification — final checklist before opening the PR

```bash
# 1. Tests still green.
pytest tests/ -v

# 2. Config round-trip with all three roles registered.
python -c "
from ollama_sentinel.config import load_config
import pathlib
c = load_config(pathlib.Path('ollama-sentinel.yaml'))
assert c.embedding.models == {
    'hot': 'qwen3-embedding:4b',
    'consolidation': 'qwen3-embedding:8b',
    'rerank': None,
}
print('OK: schema matches')
"

# 3. Legacy YAML still loads with deprecation warning.
cat > /tmp/legacy_a.yaml <<'EOF'
watch:
  directory: .
ollama:
  host: http://localhost:11434
  models:
    default:
      name: gemma3:4b
      system_prompt: test
embedding:
  enabled: true
  model: my-old-model
EOF
python -c "
from ollama_sentinel.config import load_config
import pathlib
c = load_config(pathlib.Path('/tmp/legacy_a.yaml'))
print('migrated to:', c.embedding.models['hot'])
"
# Expected: prints 'migrated to: my-old-model' AND emits a deprecation
# warning to stderr that mentions 'v0.3'.
rm /tmp/legacy_a.yaml

# 4. Live embedder works.
python -c "
import asyncio
from ollama_sentinel.context.embeddings import OllamaEmbedder
async def main():
    e = OllamaEmbedder(host='http://localhost:11434')
    v = await e.embed('hello')
    print('model:', e.model, 'dim:', len(v))
    await e.close()
asyncio.run(main())
"

# 5. Recall diff captured.
test -f docs/superpowers/notes/qwen3-recall-diff.md || echo "MISSING: run Step 8"

# 6. End-to-end watcher run on a scratch dir.
cd /tmp && mkdir -p phase_a_scratch && cd phase_a_scratch
ollama-sentinel init
echo 'def f(): return 1' > scratch.py
ollama-sentinel run &
sleep 8
ls .ollama_reviews/  # should contain a review for scratch.py
kill %1
cd - >/dev/null
```

---

## Things that look like the ticket but aren't

- **Don't wire `consolidation` into anything.** Schema only. If you find
  yourself adding a CLI flag, a code path that reads
  `models["consolidation"]`, or a model pull instruction for
  `qwen3-embedding:8b`, stop. That's Phase B.
- **Don't wire `rerank` into anything.** Schema only. If you find yourself
  creating an `OllamaReranker` class or a `RerankedSemanticRetriever`,
  stop. That's Phase C.
- **Don't pull `qwen3-embedding:8b` or any reranker model.** The schema
  registers the keys but no consumer needs them yet. Pulling unused
  ~5GB+ models on contributors' machines is needless cost.
- **Don't migrate `research_agent/core/workflow.py`'s embedder
  construction.** It currently reads `embed_cfg.get("model", "nomic-embed-text")`.
  That's the legacy shape and CB-3 (running in parallel) doesn't depend
  on it changing. A separate ticket migrates research_agent's dict-config
  path with explicit back-compat. Mixing it into Phase A inflates blast
  radius.
- **Don't promote `ImportResolver` to shared infra.** v0.3 work, documented
  in `docs/VISION.md`.
- **Don't bump the version number.** The user decides when to tag
  v0.2.0 / v0.1.1 — leave version strings alone.

---

## Risks & notes

- **Embedding dimension growth.** nomic 768 → qwen3:4b 2560. Cache
  directory size grows; per-request CPU cost grows; on contributors'
  hardware (not just M2 Max) the always-on watcher will feel slower. If
  any pre-existing benchmark exists, capture before/after numbers in the
  PR description. Cache is keyed by model name so old `embed:nomic-embed-text:*`
  entries don't collide; they become orphans (safe to `rm -rf .embed_cache`).
- **The `extra="forbid"` policy is a behavioral change.** Existing YAMLs
  with stray fields under `embedding:` will start failing to load. This
  is intentional — typos should surface — but it means the rollout might
  break user configs. Document loudly in CLAUDE.md.
- **The legacy migration deprecation warning is the only signal users
  get.** The v0.3 hard-error timeline is committed in this PR's commit
  message and CLAUDE.md "Recent landings." When v0.3 ships, the validator
  must remove the migration path and raise on legacy `model:` outright.
- **The before/after diff is a soft signal.** If the violation DB is empty
  or thin, the diff will be uninformative. If that's the case, run the
  watcher against a real codebase first to populate the DB, then re-run
  the diff capture.
- **`research_agent` continues running on legacy `embedding.model`.** That's
  intentional. Out of scope for Phase A.

---

## PR description template

```
Phase A: hot-path swap to qwen3-embedding:4b + pre-registered role schema

Refactor EmbeddingConfig from a flat `model: str` to a named-role dict
mirroring OllamaConfig.models. Default flips to qwen3-embedding:4b on the
hot path. Pre-registers `consolidation` (qwen3-embedding:8b) and `rerank`
(None) keys in the schema even though no consumer reads them yet, so Phases
B and C don't need another config migration when they land.

Changes:
- EmbeddingConfig: models: Dict[str, Optional[str]] with extra="forbid"
- model_validator(mode="before"): legacy `embedding.model` -> models.hot
  with deprecation warning that threatens v0.3 hard-error
- field_validator: requires non-empty `hot`; allows None for other roles
- OllamaEmbedder default model: qwen3-embedding:4b
- FileProcessor reads config.embedding.models["hot"]
- create_default_config writes the new shape
- ollama-sentinel.yaml gains an explicit embedding: block
- Docs: README, CLAUDE.md, docs/GUIDE.md updated

Receipts:
- All <N+10> tests pass; full suite ~<runtime>s
- Live embedder smoke-tested against Ollama
- Before/after recall diff captured at
  docs/superpowers/notes/qwen3-recall-diff.md
- Legacy YAML round-trip verified (embedding.model -> models.hot)

Out of scope:
- consolidation role unwired (Phase B, parked)
- rerank role unwired (Phase C, parked)
- research_agent's embedder construction unchanged (separate ticket)
```

---

## Ground truth at the time this spec was written

Repo at HEAD:
- `ollama_sentinel/models.py:131-134` — flat 2-field EmbeddingConfig.
- `ollama_sentinel/config.py:124-127` — flat embedding default.
- `ollama_sentinel/context/embeddings.py:34` — default
  `model="nomic-embed-text"`.
- `ollama_sentinel/processor.py` — exactly one call site reading
  `config.embedding.model`.
- `ollama-sentinel.yaml` — no embedding: block (defaults apply).
- `tests/test_models.py:202-205` — TestEmbeddingConfig.test_defaults
  asserts `cfg.model == "nomic-embed-text"`.
- `tests/test_config.py:91-93` — test_emits_embedding_section asserts
  `config["embedding"]["model"] == "nomic-embed-text"`.

If any of the above has changed by the time this ticket is picked up,
re-confirm before applying the diff.
