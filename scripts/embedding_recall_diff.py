"""Capture before/after semantic-recall snapshots for a real codebase.

Spec deviation §3 from docs/superpowers/plans/2026-05-01-phase-a-qwen3-
hot-path-swap.md: the script accepts the embedder model as a CLI argument
so before/after can both run on the post-Phase-A code without a stash
dance, and so the script never depends on a particular EmbeddingConfig
shape.

Usage:
    python scripts/embedding_recall_diff.py before nomic-embed-text
    python scripts/embedding_recall_diff.py after  qwen3-embedding:4b
    python scripts/embedding_recall_diff.py compare

Writes:
    /tmp/recall_diff_before.json
    /tmp/recall_diff_after.json
    docs/superpowers/notes/qwen3-recall-diff.md  (compare step)
"""
from __future__ import annotations
import asyncio
import json
import pathlib
import sys
from typing import List

from ollama_sentinel.context.embeddings import OllamaEmbedder
from ollama_sentinel.violation_db import ViolationDB

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
DB_PATH_RELATIVE = ".ollama_reviews/memory.db"

TARGET_FILES: List[str] = [
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

_MIN_FINDINGS_FOR_DIFF = 50


async def capture(out_path: pathlib.Path, model_name: str) -> None:
    embedder = OllamaEmbedder(host="http://localhost:11434", model=model_name)
    db_path = REPO_ROOT / DB_PATH_RELATIVE
    if not db_path.exists():
        out_path.write_text(json.dumps(
            {"model": model_name, "files": {}, "note": f"no DB at {db_path}"},
            indent=2,
        ))
        print(f"wrote {out_path} (no DB)")
        await embedder.close()
        return
    db = ViolationDB(str(db_path))
    try:
        report = {"model": model_name, "files": {}}
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

    total_neighbors = sum(
        len(snapshot["files"].get(rel, {}).get("neighbors", []))
        for snapshot in (before, after)
        for rel in snapshot.get("files", {})
    )
    if total_neighbors < _MIN_FINDINGS_FOR_DIFF:
        out.write_text(
            f"# Qwen3 recall diff (sparse)\n\n"
            f"Before: `{before['model']}`\n"
            f"After:  `{after['model']}`\n\n"
            f"Combined neighbor count across both snapshots: {total_neighbors} "
            f"(threshold {_MIN_FINDINGS_FOR_DIFF}).\n\n"
            f"The violation DB is too sparse to produce a meaningful "
            f"before/after comparison. This is acceptable for Phase A — "
            f"the schema swap stands on its own — but capture a richer "
            f"diff when the DB has accumulated more findings (e.g. after "
            f"running the watcher against a real project for a few days).\n"
        )
        print(f"wrote {out} (sparse-DB note)")
        return

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


def _usage() -> None:
    sys.exit(
        "usage: embedding_recall_diff.py {before|after} <model-name>\n"
        "       embedding_recall_diff.py compare"
    )


if __name__ == "__main__":
    if len(sys.argv) < 2:
        _usage()
    mode = sys.argv[1]
    if mode == "compare":
        compare()
    elif mode in ("before", "after"):
        if len(sys.argv) != 3:
            _usage()
        out = pathlib.Path(f"/tmp/recall_diff_{mode}.json")
        asyncio.run(capture(out, sys.argv[2]))
    else:
        _usage()
