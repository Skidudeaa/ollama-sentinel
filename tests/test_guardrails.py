"""Tests for ollama_sentinel.guardrails — shape clustering + candidate detection."""

from ollama_sentinel.context.embeddings import EmbeddingUnavailable
from ollama_sentinel.guardrails import Candidate, detect_candidates


class _FakeEmbedder:
    """Maps a finding's cache_key (or embed_text) to a fixed vector.

    Raises EmbeddingUnavailable for anything unmapped — mirrors the real
    embedder's failure mode so degradation paths are exercised.
    """
    def __init__(self, vectors: dict):
        self._vectors = vectors

    async def embed(self, text, *, cache_key=None):
        if cache_key in self._vectors:
            return self._vectors[cache_key]
        if text in self._vectors:
            return self._vectors[text]
        raise EmbeddingUnavailable(f"no vector for {cache_key or text!r}")


def _finding(fid, category, description, *, file_path="src/a.py", embed_text=None):
    return {
        "id": fid,
        "category": category,
        "severity": "high",
        "description": description,
        "file_path": file_path,
        "line_start": fid,
        "line_end": fid,
        "embed_text": embed_text or f"[{category}] {description}",
        "guardrail_id": None,
        "confirming_signals": ["manual_confirm"],
    }


class TestDetectCandidates:
    async def test_three_similar_same_category_form_one_candidate(self):
        findings = [
            _finding(1, "security", "eval on user input"),
            _finding(2, "security", "eval of request body"),
            _finding(3, "security", "eval of untrusted str"),
        ]
        embedder = _FakeEmbedder({
            "finding:1": [1.0, 0.0],
            "finding:2": [0.99, 0.01],
            "finding:3": [0.98, 0.02],
        })
        cands = await detect_candidates(findings, embedder, similarity_threshold=0.9)
        assert len(cands) == 1
        c = cands[0]
        assert isinstance(c, Candidate)
        assert c.category == "security"
        assert sorted(c.finding_ids) == [1, 2, 3]
        assert c.size == 3

    async def test_two_corroborated_below_threshold_count(self):
        """Only two distinct findings in a shape → below the >=3 threshold."""
        findings = [
            _finding(1, "security", "eval one"),
            _finding(2, "security", "eval two"),
        ]
        embedder = _FakeEmbedder({"finding:1": [1.0, 0.0], "finding:2": [1.0, 0.0]})
        cands = await detect_candidates(findings, embedder, similarity_threshold=0.9)
        assert cands == []

    async def test_single_finding_many_incidents_not_a_candidate(self):
        """Distinct *findings* drive the threshold, not incident count."""
        f = _finding(1, "security", "eval once")
        f["confirming_signals"] = ["test_failure", "manual_confirm", "fix_commit"]
        embedder = _FakeEmbedder({"finding:1": [1.0, 0.0]})
        cands = await detect_candidates([f], embedder, similarity_threshold=0.9)
        assert cands == []

    async def test_same_category_dissimilar_no_candidate(self):
        findings = [
            _finding(1, "security", "eval"),
            _finding(2, "security", "weak hash"),
            _finding(3, "security", "open redirect"),
        ]
        embedder = _FakeEmbedder({
            "finding:1": [1.0, 0.0, 0.0],
            "finding:2": [0.0, 1.0, 0.0],
            "finding:3": [0.0, 0.0, 1.0],
        })
        cands = await detect_candidates(findings, embedder, similarity_threshold=0.9)
        assert cands == []

    async def test_cross_category_not_merged(self):
        """Identical embeddings across categories do not merge into one shape."""
        findings = [
            _finding(1, "security", "eval a"),
            _finding(2, "security", "eval b"),
            _finding(3, "perf", "n^2 loop"),
        ]
        embedder = _FakeEmbedder({
            "finding:1": [1.0, 0.0],
            "finding:2": [1.0, 0.0],
            "finding:3": [1.0, 0.0],  # same vector, different category
        })
        cands = await detect_candidates(findings, embedder, similarity_threshold=0.9)
        # security cluster = 2 (<3), perf cluster = 1 → no candidate.
        assert cands == []

    async def test_candidate_carries_member_metadata(self):
        findings = [
            _finding(1, "bug", "off by one", file_path="src/a.py"),
            _finding(2, "bug", "off by one too", file_path="src/b.py"),
            _finding(3, "bug", "another off by one", file_path="src/c.py"),
        ]
        embedder = _FakeEmbedder({
            "finding:1": [1.0, 0.0],
            "finding:2": [1.0, 0.0],
            "finding:3": [1.0, 0.0],
        })
        cands = await detect_candidates(findings, embedder, similarity_threshold=0.9)
        assert len(cands) == 1
        c = cands[0]
        assert set(c.descriptions) == {"off by one", "off by one too", "another off by one"}
        assert set(c.file_paths) == {"src/a.py", "src/b.py", "src/c.py"}

    async def test_embedding_unavailable_finding_is_skipped(self):
        """A finding the embedder can't embed drops out, not crashes the run."""
        findings = [
            _finding(1, "security", "eval a"),
            _finding(2, "security", "eval b"),
            _finding(3, "security", "eval c"),  # unmapped → skipped
        ]
        embedder = _FakeEmbedder({
            "finding:1": [1.0, 0.0],
            "finding:2": [1.0, 0.0],
            # finding:3 missing → EmbeddingUnavailable
        })
        cands = await detect_candidates(findings, embedder, similarity_threshold=0.9)
        # Only 2 embeddable findings remain → below threshold.
        assert cands == []

    async def test_empty_input_returns_empty(self):
        embedder = _FakeEmbedder({})
        assert await detect_candidates([], embedder) == []
