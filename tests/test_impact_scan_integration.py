"""Integration test: the real impact_scan node inside a real LangGraph compile.

The existing impact_scan tests in test_research_agent.py mirror the node's
entity-parsing and severity logic in standalone copies — they never run the
node itself. This test drives the actual `impact_scan` closure built by
`build_workflow` (a genuine LangGraph compile), with only the LLM boundary
faked, against a temp repo. It exercises the real ImportResolver, AST
call-site matching, severity classification, LLM-migration plumbing, and
ImpactAnalysis assembly end-to-end.

Requires the [research] extras (langgraph, langchain). Skipped if unavailable.
"""
import pytest

pytest.importorskip("langgraph")
pytest.importorskip("langchain_openai")
# build_workflow pulls the full research stack transitively (e.g.
# langchain_huggingface via the embedding util). Skip cleanly where the
# [research] extras are only partially installed.
pytest.importorskip("research_agent.core.workflow")

from research_agent.core.config import Config
from research_agent.core.models import ResearchSession


class _FakeMessage:
    def __init__(self, content: str):
        self.content = content


class _FakeLLM:
    """Deterministic stand-in for ChatOpenAI used inside impact_scan.

    Distinguishes the node's two prompts by a stable substring and returns
    canned, parseable responses — no network, no API key needed.
    """

    def __init__(self, *args, **kwargs):
        pass

    def invoke(self, prompt: str) -> _FakeMessage:
        if "Return ONLY a JSON array" in prompt:
            return _FakeMessage('["requests.get"]')
        if "suggest a brief migration" in prompt:
            return _FakeMessage("api.py:99 -> Replace requests.get with httpx.get")
        return _FakeMessage("")


def _make_config(tmp_path):
    Config.reset()
    cfg = Config()._config
    cfg["memory"] = dict(cfg["memory"])
    cfg["memory"]["cache_path"] = str(tmp_path / "cache")
    cfg["memory"]["db_path"] = str(tmp_path / "db")
    return cfg


def _state(session):
    return {
        "session": session,
        "step": "impact_scan",
        "search_results": None,
        "content_items": [],
        "code_results": "",
        "answer": None,
        "confidence": None,
        "verification": None,
        "refined_queries": None,
        "final_answer": None,
        "impact_analysis": None,
    }


def test_impact_scan_runs_in_real_compiled_graph(tmp_path, monkeypatch):
    monkeypatch.setattr("research_agent.core.workflow.ChatOpenAI", _FakeLLM)
    from research_agent.core.workflow import build_workflow

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "api.py").write_text(
        "import requests\n"
        "\n"
        "def fetch(url):\n"
        "    return requests.get(url)\n"
    )

    cfg = _make_config(tmp_path)
    compiled, components = build_workflow(
        cfg, repo_path=str(repo), openai_api_key="test-key", serpapi_api_key=None
    )

    # The graph really compiled and impact_scan is wired in.
    assert type(compiled).__name__ == "CompiledStateGraph"
    assert "impact_scan" in compiled.nodes

    # Drive the REAL node closure (not a mirror).
    session = ResearchSession(query="requests.get is being removed", code_context="")
    out = compiled.nodes["impact_scan"].bound.invoke(_state(session))

    impact = out["impact_analysis"]
    assert impact is not None
    assert impact.entity_count == 1

    # Real ImportResolver + AST matching located the call site in api.py.
    assert any(it.file_path.endswith("api.py") for it in impact.items)
    # affected_files is a list of path strings, sorted.
    assert any(f.endswith("api.py") for f in impact.affected_files)

    # "removed" in the query => HIGH severity classification.
    high = [it for it in impact.items if it.severity == "HIGH"]
    assert high, "requests.get usage should be HIGH (query says 'removed')"

    # The faked LLM migration suggestion was parsed back onto the HIGH item.
    assert any("httpx" in (it.action or "") for it in high)


def test_impact_scan_no_entities_passes_through(tmp_path, monkeypatch):
    """When the LLM extracts no entities, the node sets impact_analysis=None."""

    class _NoEntityLLM(_FakeLLM):
        def invoke(self, prompt: str) -> _FakeMessage:
            if "Return ONLY a JSON array" in prompt:
                return _FakeMessage("[]")
            return _FakeMessage("")

    monkeypatch.setattr("research_agent.core.workflow.ChatOpenAI", _NoEntityLLM)
    from research_agent.core.workflow import build_workflow

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "api.py").write_text("x = 1\n")

    cfg = _make_config(tmp_path)
    compiled, _ = build_workflow(
        cfg, repo_path=str(repo), openai_api_key="test-key", serpapi_api_key=None
    )

    session = ResearchSession(query="nothing concrete here", code_context="")
    out = compiled.nodes["impact_scan"].bound.invoke(_state(session))
    assert out["impact_analysis"] is None
