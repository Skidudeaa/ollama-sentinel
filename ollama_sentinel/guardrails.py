"""Guardrail shape clustering + candidate detection (Phase 2).

Leaf module: the clustering logic is pure enough to test with a fake embedder
(no Ollama, no DB). Candidate detection runs *on demand* — when the developer
lists candidates — never on the review hot path (KTD4).
"""
from __future__ import annotations

import asyncio
import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import List

from ollama_sentinel.context.embeddings import EmbeddingUnavailable


@dataclass
class Candidate:
    """A detected guardrail candidate: a cluster of same-shape findings.

    A candidate is a proposal, not a guardrail — the developer confirms or
    dismisses it (U7). ``finding_ids`` are the distinct corroborated findings
    that formed the shape; ``descriptions``/``file_paths`` carry member context
    for drafting an assertion and deriving a scope.
    """
    category: str
    finding_ids: List[int] = field(default_factory=list)
    descriptions: List[str] = field(default_factory=list)
    file_paths: List[str] = field(default_factory=list)

    @property
    def size(self) -> int:
        return len(self.finding_ids)


def _cosine(a, b) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _embed_text_for(f: dict) -> str:
    """Text to embed for a finding — its stored ``embed_text`` or a fallback."""
    return f.get("embed_text") or (
        f"[{f.get('severity', '')}] {f.get('category', '')}: {f.get('description', '')}"
    )


async def _embed_findings(findings: List[dict], embedder):
    """Embed each finding, dropping any the embedder cannot handle.

    A finding the embedder rejects (``EmbeddingUnavailable``) is silently
    excluded rather than aborting the whole run — detection degrades, never
    crashes.
    """
    async def _one(f):
        try:
            vec = await embedder.embed(
                _embed_text_for(f), cache_key=f"finding:{f['id']}",
            )
        except EmbeddingUnavailable:
            return None
        return (f, vec)

    pairs = await asyncio.gather(*(_one(f) for f in findings))
    return [p for p in pairs if p is not None]


def _cluster(pairs, threshold: float):
    """Greedy seed clustering.

    A finding joins the first existing cluster whose *seed* vector is within
    ``threshold`` cosine similarity; otherwise it seeds a new cluster. Input
    order is deterministic (caller sorts by id), so clusters are reproducible.
    """
    clusters: list = []  # each: {"seed": vec, "members": [finding, ...]}
    for f, vec in pairs:
        for c in clusters:
            if _cosine(vec, c["seed"]) >= threshold:
                c["members"].append(f)
                break
        else:
            clusters.append({"seed": vec, "members": [f]})
    return clusters


async def detect_candidates(
    findings: List[dict],
    embedder,
    *,
    similarity_threshold: float = 0.85,
    min_distinct: int = 3,
) -> List[Candidate]:
    """Detect guardrail candidates from corroborated findings.

    ``findings`` are corroborated findings (each with >=1 incident; see
    ``ViolationDB.get_corroborated_findings``). They are grouped by category and
    clustered within category by embedding cosine similarity; a cluster with at
    least ``min_distinct`` (default 3) *distinct* findings becomes a Candidate.
    Cross-category findings never merge (grouping precedes clustering), and the
    threshold counts distinct findings — not incident counts.

    Embedding goes through the injected async ``embedder`` (duck-typed:
    ``embed(text, *, cache_key=None)``), so a fake embedder drives the tests and
    the real ``OllamaEmbedder`` drives production. Deterministic: findings are
    processed in id order.
    """
    if not findings:
        return []

    by_category: "defaultdict[str, list]" = defaultdict(list)
    for f in sorted(findings, key=lambda r: r.get("id", 0)):
        by_category[f.get("category", "")].append(f)

    candidates: List[Candidate] = []
    for category, group in by_category.items():
        pairs = await _embed_findings(group, embedder)
        for c in _cluster(pairs, similarity_threshold):
            members = c["members"]
            if len(members) >= min_distinct:
                candidates.append(Candidate(
                    category=category,
                    finding_ids=[m["id"] for m in members],
                    descriptions=[m.get("description", "") for m in members],
                    file_paths=[m.get("file_path", "") for m in members],
                ))
    return candidates


# ---------------------------------------------------------------------------
# Candidate surfacing + curation (U7)
# ---------------------------------------------------------------------------


def candidate_signature(candidate: Candidate) -> str:
    """A stable, order-independent signature for a candidate's shape.

    Used to suppress re-proposal of a dismissed shape: the same set of member
    descriptions in the same category yields the same signature regardless of
    finding order, so a dismissed candidate stays dismissed across runs.
    """
    descs = "|".join(sorted(candidate.descriptions))
    return f"{candidate.category}::{descs}"


def derive_scope(candidate: Candidate):
    """Derive a (scope_category, scope_path_glob) for a promoted guardrail.

    Category is always the cluster's category. A path glob is suggested only
    when every member shares one top-level directory (e.g. all under ``src/`` →
    ``src/*``); mixed or root-level files yield no path scope (applies broadly).
    The developer can edit either at confirmation.
    """
    category = candidate.category
    tops = set()
    for p in candidate.file_paths:
        parts = p.replace("\\", "/").split("/")
        tops.add(parts[0] if len(parts) > 1 else "")
    if len(tops) == 1:
        top = next(iter(tops))
        return category, (f"{top}/*" if top else None)
    return category, None


def _draft_prompt(candidate: Candidate) -> str:
    examples = "\n".join(f"- {d}" for d in candidate.descriptions[:8])
    return (
        f"These recurring {candidate.category} issues were each independently "
        f"confirmed in this codebase:\n{examples}\n\n"
        "Write ONE imperative sentence — a project guardrail — that, if followed, "
        "would prevent this class of issue. Output only the sentence, no preamble."
    )


def _fallback_assertion(candidate: Candidate) -> str:
    first = candidate.descriptions[0] if candidate.descriptions else candidate.category
    return f"Avoid recurring {candidate.category} issues such as: {first}"


async def draft_assertion(candidate: Candidate, model_call=None) -> str:
    """Draft a one-line NL assertion for a candidate via the local model.

    ``model_call`` is an async callable ``(prompt: str) -> str`` (the CLI wires
    it to the configured model). It is best-effort: with no model, a model
    error, or a blank reply, a deterministic minimal fallback is returned so a
    candidate always lists with *some* editable assertion (KTD5). The developer
    edits the result at confirmation regardless.
    """
    if model_call is None:
        return _fallback_assertion(candidate)
    try:
        text = await model_call(_draft_prompt(candidate))
    except Exception:
        return _fallback_assertion(candidate)
    text = (text or "").strip()
    return text or _fallback_assertion(candidate)


def filter_suppressed(candidates: List[Candidate], dismissed_signatures) -> List[Candidate]:
    """Drop candidates whose shape was previously dismissed.

    ``dismissed_signatures`` is the set of ``candidate_signature`` values stored
    when candidates were rejected, so the same shape is not re-proposed.
    """
    sigs = set(dismissed_signatures)
    return [c for c in candidates if candidate_signature(c) not in sigs]
