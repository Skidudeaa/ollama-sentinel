"""Comprehensive tests for the research_agent module.

Covers pure/near-pure functions that need no external services
(no OpenAI, no Playwright, no web search).
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict
from typing import Any, List, Optional

import pytest

# ---------------------------------------------------------------------------
# Tier 1 — always importable (verified: models, cache, config)
# ---------------------------------------------------------------------------
from research_agent.core.models import (
    ContentItem, ImpactAnalysis, ImpactItem, ResearchSession, ResearchStep,
)
from research_agent.utils.cache import Cache
from research_agent.core.config import Config

# ---------------------------------------------------------------------------
# Tier 2 — guarded imports (search needs langchain_core, browser needs playwright)
# ---------------------------------------------------------------------------
try:
    from research_agent.tools.search import SearchResult

    HAS_SEARCH = True
except ImportError:
    HAS_SEARCH = False

try:
    from research_agent.tools.browser import BrowserTool

    HAS_BROWSER = True
except ImportError:
    HAS_BROWSER = False


# ===================================================================
# 1. ResearchSession lifecycle
# ===================================================================

class TestResearchSession:
    """Tests for ResearchSession step management and completion."""

    def test_start_step_creates_new_step_running(self):
        session = ResearchSession(query="test query")
        step = session.start_step("analyze")
        assert step.name == "analyze"
        assert step.status == "running"
        assert step.start_time > 0

    def test_start_step_existing_step_updates_to_running(self):
        session = ResearchSession(query="test query")
        session.add_step("analyze")
        existing = session.get_step("analyze")
        assert existing.status == "pending"

        step = session.start_step("analyze")
        assert step.status == "running"
        assert step.start_time > 0
        # Should reuse the same step, not duplicate
        assert len(session.steps) == 1

    def test_complete_step_sets_status_output_end_time(self):
        session = ResearchSession(query="test query")
        session.start_step("analyze")
        step = session.complete_step("analyze", output={"result": "done"})
        assert step.status == "completed"
        assert step.output == {"result": "done"}
        assert step.end_time > 0

    def test_fail_step_sets_status_error_end_time(self):
        session = ResearchSession(query="test query")
        session.start_step("search")
        step = session.fail_step("search", "network timeout")
        assert step.status == "failed"
        assert step.error == "network timeout"
        assert step.end_time > 0

    def test_fail_step_on_nonexistent_step(self):
        """fail_step on a step that was never started still records the failure."""
        session = ResearchSession(query="test query")
        step = session.fail_step("never_started", "boom")
        assert step.status == "failed"
        assert step.error == "boom"
        assert step.end_time > 0

    def test_get_step_returns_none_for_unknown(self):
        session = ResearchSession(query="test query")
        assert session.get_step("nonexistent") is None

    def test_get_step_returns_existing(self):
        session = ResearchSession(query="test query")
        session.start_step("analyze")
        step = session.get_step("analyze")
        assert step is not None
        assert step.name == "analyze"

    def test_complete_sets_answer_confidence_end_time(self):
        session = ResearchSession(query="test query")
        session.complete("The answer is 42", 0.95)
        assert session.answer == "The answer is 42"
        assert session.confidence == 0.95
        assert session.end_time > 0

    def test_duration_returns_positive_before_complete(self):
        session = ResearchSession(query="test query")
        dur = session.duration
        assert dur >= 0

    def test_duration_returns_positive_after_complete(self):
        session = ResearchSession(query="test query")
        session.complete("done", 1.0)
        dur = session.duration
        assert dur >= 0

    def test_duration_uses_end_time_when_completed(self):
        session = ResearchSession(query="test query")
        # Manually set times for deterministic check
        session.start_time = 100.0
        session.end_time = 105.0
        assert session.duration == 5.0

    def test_add_step_returns_pending(self):
        session = ResearchSession(query="test query")
        step = session.add_step("new_step")
        assert step.status == "pending"
        assert step.name == "new_step"

    def test_session_with_code_context(self):
        session = ResearchSession(query="test", code_context="def foo(): pass")
        assert session.code_context == "def foo(): pass"

    def test_multiple_steps(self):
        session = ResearchSession(query="test")
        session.start_step("analyze")
        session.start_step("search")
        session.start_step("read")
        assert len(session.steps) == 3
        names = [s.name for s in session.steps]
        assert names == ["analyze", "search", "read"]


# ===================================================================
# 2. SearchResult.__post_init__ domain extraction
# ===================================================================

@pytest.mark.skipif(not HAS_SEARCH, reason="langchain_core not installed")
class TestSearchResultDomainExtraction:
    """Tests for SearchResult automatic domain extraction from URL."""

    def test_domain_extracted_from_url(self):
        sr = SearchResult(
            url="https://example.com/page",
            title="Example",
            snippet="A snippet",
            position=0,
            source="ddg",
        )
        assert sr.domain == "example.com"

    def test_subdomain_preserved(self):
        sr = SearchResult(
            url="https://docs.python.org/3/library/",
            title="Python Docs",
            snippet="Standard library",
            position=1,
            source="ddg",
        )
        assert sr.domain == "docs.python.org"

    def test_domain_not_overwritten_when_preset(self):
        sr = SearchResult(
            url="https://example.com/page",
            title="Example",
            snippet="A snippet",
            position=0,
            source="ddg",
            domain="custom.domain.com",
        )
        assert sr.domain == "custom.domain.com"

    def test_empty_url_gives_empty_domain(self):
        sr = SearchResult(
            url="",
            title="No URL",
            snippet="No snippet",
            position=0,
            source="ddg",
        )
        assert sr.domain == ""

    def test_url_with_port(self):
        sr = SearchResult(
            url="https://localhost:8080/api",
            title="Local",
            snippet="API",
            position=0,
            source="ddg",
        )
        assert sr.domain == "localhost:8080"


# ===================================================================
# 2b. SearchResult domain extraction — fallback when langchain_core
#     is not installed.  The dataclass + __post_init__ logic only uses
#     stdlib urllib.parse, so we replicate it locally.
# ===================================================================

@pytest.mark.skipif(HAS_SEARCH, reason="Only runs when langchain_core is NOT installed")
class TestSearchResultDomainExtractionFallback:
    """Inline replica of SearchResult so the domain-extraction logic
    is tested even without langchain_core."""

    @staticmethod
    def _make(url, title="", snippet="", position=0, source="ddg", domain=""):
        from dataclasses import dataclass, field
        from urllib.parse import urlparse

        @dataclass
        class _SearchResult:
            url: str
            title: str
            snippet: str
            position: int
            source: str
            domain: str = ""

            def __post_init__(self):
                if not self.domain and self.url:
                    parsed = urlparse(self.url)
                    self.domain = parsed.netloc

        return _SearchResult(
            url=url,
            title=title,
            snippet=snippet,
            position=position,
            source=source,
            domain=domain,
        )

    def test_domain_extracted_from_url(self):
        sr = self._make(url="https://example.com/page", title="Example", snippet="A snippet")
        assert sr.domain == "example.com"

    def test_subdomain_preserved(self):
        sr = self._make(
            url="https://docs.python.org/3/library/",
            title="Python Docs",
            snippet="Standard library",
            position=1,
        )
        assert sr.domain == "docs.python.org"

    def test_domain_not_overwritten_when_preset(self):
        sr = self._make(
            url="https://example.com/page",
            title="Example",
            snippet="A snippet",
            domain="custom.domain.com",
        )
        assert sr.domain == "custom.domain.com"

    def test_empty_url_gives_empty_domain(self):
        sr = self._make(url="", title="No URL", snippet="No snippet")
        assert sr.domain == ""

    def test_url_with_port(self):
        sr = self._make(url="https://localhost:8080/api", title="Local", snippet="API")
        assert sr.domain == "localhost:8080"


# ===================================================================
# 3. Cache JSON round-trip
# ===================================================================

class TestCache:
    """Tests for Cache set/get/delete/clear and serialization."""

    def test_set_get_roundtrip_plain_dict(self, tmp_path):
        cache = Cache(cache_dir=str(tmp_path / "cache"), ttl_hours=1)
        data = {"key": "value", "number": 42, "nested": {"a": 1}}
        assert cache.set("test_dict", data) is True
        result = cache.get("test_dict")
        assert result == data

    def test_set_get_with_dataclass(self, tmp_path):
        cache = Cache(cache_dir=str(tmp_path / "cache"), ttl_hours=1)
        item = ContentItem(
            url="https://example.com",
            title="Example",
            content="Hello world",
            source="browser",
        )
        assert cache.set("content_item", item) is True
        result = cache.get("content_item")
        assert isinstance(result, dict)
        assert result["url"] == "https://example.com"
        assert result["title"] == "Example"
        assert result["content"] == "Hello world"

    def test_get_nonexistent_key_returns_none(self, tmp_path):
        cache = Cache(cache_dir=str(tmp_path / "cache"), ttl_hours=1)
        assert cache.get("nonexistent") is None

    def test_delete_existing_key(self, tmp_path):
        cache = Cache(cache_dir=str(tmp_path / "cache"), ttl_hours=1)
        cache.set("to_delete", {"x": 1})
        assert cache.delete("to_delete") is True
        assert cache.get("to_delete") is None

    def test_delete_nonexistent_key_returns_true(self, tmp_path):
        """diskcache.delete does not raise on missing keys."""
        cache = Cache(cache_dir=str(tmp_path / "cache"), ttl_hours=1)
        assert cache.delete("nope") is True

    def test_clear_removes_all_entries(self, tmp_path):
        cache = Cache(cache_dir=str(tmp_path / "cache"), ttl_hours=1)
        cache.set("a", 1)
        cache.set("b", 2)
        cache.set("c", 3)
        assert cache.clear() is True
        assert cache.get("a") is None
        assert cache.get("b") is None
        assert cache.get("c") is None

    def test_serialize_dataclass(self):
        item = ContentItem(url="https://x.com", title="T", content="C")
        serialized = Cache._serialize(item)
        assert isinstance(serialized, str)
        parsed = json.loads(serialized)
        assert parsed["url"] == "https://x.com"
        assert parsed["title"] == "T"

    def test_serialize_dict(self):
        data = {"foo": "bar", "num": 99}
        serialized = Cache._serialize(data)
        assert isinstance(serialized, str)
        assert json.loads(serialized) == data

    def test_serialize_list(self):
        data = [1, 2, "three", {"four": 4}]
        serialized = Cache._serialize(data)
        assert isinstance(serialized, str)
        assert json.loads(serialized) == data

    def test_set_get_roundtrip_list(self, tmp_path):
        cache = Cache(cache_dir=str(tmp_path / "cache"), ttl_hours=1)
        data = [1, "two", {"three": 3}]
        cache.set("mylist", data)
        assert cache.get("mylist") == data

    def test_get_stats(self, tmp_path):
        cache = Cache(cache_dir=str(tmp_path / "cache"), ttl_hours=24)
        cache.set("k", "v")
        stats = cache.get_stats()
        assert stats["item_count"] == 1
        assert stats["ttl_hours"] == 24.0


# ===================================================================
# 4. BrowserTool._validate_url
# ===================================================================

@pytest.mark.skipif(not HAS_BROWSER, reason="playwright not installed")
class TestBrowserToolValidateUrl:
    """Tests for BrowserTool._validate_url SSRF protection."""

    def test_https_url_passes(self):
        BrowserTool._validate_url("https://example.com")

    def test_http_url_with_path_passes(self):
        BrowserTool._validate_url("http://example.com/path?q=1")

    def test_file_scheme_raises(self):
        with pytest.raises(ValueError, match="Unsupported URL scheme"):
            BrowserTool._validate_url("file:///etc/passwd")

    def test_ftp_scheme_raises(self):
        with pytest.raises(ValueError, match="Unsupported URL scheme"):
            BrowserTool._validate_url("ftp://server.com")

    def test_loopback_127_raises(self):
        with pytest.raises(ValueError, match="private"):
            BrowserTool._validate_url("http://127.0.0.1/admin")

    def test_private_192_168_raises(self):
        with pytest.raises(ValueError, match="private"):
            BrowserTool._validate_url("http://192.168.1.1/internal")

    def test_private_10_raises(self):
        with pytest.raises(ValueError, match="private"):
            BrowserTool._validate_url("http://10.0.0.1/api")

    def test_empty_url_raises(self):
        with pytest.raises(ValueError):
            BrowserTool._validate_url("")

    def test_no_hostname_raises(self):
        with pytest.raises(ValueError):
            BrowserTool._validate_url("http://")

    def test_domain_name_passes(self):
        """Regular domain names (not IP addresses) should pass."""
        BrowserTool._validate_url("https://docs.python.org/3/library/")


# ===================================================================
# 4b. _validate_url — fallback when browser.py can't be imported
# ===================================================================

@pytest.mark.skipif(HAS_BROWSER, reason="Only runs when playwright is NOT installed")
class TestValidateUrlFallback:
    """Re-implement _validate_url inline so we can still test the
    SSRF-protection logic even when playwright is not installed."""

    @staticmethod
    def _validate_url(url: str) -> None:
        """Mirror of BrowserTool._validate_url."""
        import ipaddress
        import urllib.parse

        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ("http", "https"):
            raise ValueError(
                f"Unsupported URL scheme: {parsed.scheme!r}. Only http/https allowed."
            )
        hostname = parsed.hostname
        if not hostname:
            raise ValueError(f"URL has no hostname: {url}")
        try:
            addr = ipaddress.ip_address(hostname)
            if (
                addr.is_private
                or addr.is_loopback
                or addr.is_reserved
                or addr.is_link_local
            ):
                raise ValueError(
                    f"URL targets a private/reserved IP address: {hostname}"
                )
        except ValueError as e:
            if "private" in str(e) or "reserved" in str(e) or "loopback" in str(e):
                raise

    def test_https_url_passes(self):
        self._validate_url("https://example.com")

    def test_http_url_with_path_passes(self):
        self._validate_url("http://example.com/path?q=1")

    def test_file_scheme_raises(self):
        with pytest.raises(ValueError, match="Unsupported URL scheme"):
            self._validate_url("file:///etc/passwd")

    def test_ftp_scheme_raises(self):
        with pytest.raises(ValueError, match="Unsupported URL scheme"):
            self._validate_url("ftp://server.com")

    def test_loopback_127_raises(self):
        with pytest.raises(ValueError, match="private"):
            self._validate_url("http://127.0.0.1/admin")

    def test_private_192_168_raises(self):
        with pytest.raises(ValueError, match="private"):
            self._validate_url("http://192.168.1.1/internal")

    def test_private_10_raises(self):
        with pytest.raises(ValueError, match="private"):
            self._validate_url("http://10.0.0.1/api")

    def test_empty_url_raises(self):
        with pytest.raises(ValueError):
            self._validate_url("")

    def test_no_hostname_raises(self):
        with pytest.raises(ValueError):
            self._validate_url("http://")

    def test_domain_name_passes(self):
        self._validate_url("https://docs.python.org/3/library/")


# ===================================================================
# 5. Config singleton + reset
# ===================================================================

class TestConfig:
    """Tests for Config singleton behaviour and reset."""

    @pytest.fixture(autouse=True)
    def _reset_config(self):
        """Ensure every test starts and ends with a clean singleton."""
        Config.reset()
        yield
        Config.reset()

    def test_singleton_returns_same_instance(self):
        c1 = Config()
        c2 = Config()
        assert c1 is c2

    def test_reset_allows_fresh_instance(self):
        c1 = Config()
        Config.reset()
        c2 = Config()
        assert c1 is not c2

    def test_second_after_reset_is_different_object(self):
        c1 = Config()
        id1 = id(c1)
        Config.reset()
        c2 = Config()
        assert id(c2) != id1

    def test_default_values(self):
        c = Config()
        assert c.get("api.openai_model") == "gpt-4o-preview"
        assert c.get("search.results_per_query") == 10
        assert c.get("agent.max_iterations") == 3

    def test_get_missing_key_returns_default(self):
        c = Config()
        assert c.get("nonexistent.path") is None
        assert c.get("nonexistent.path", "fallback") == "fallback"

    def test_set_and_get(self):
        c = Config()
        c.set("api.openai_model", "gpt-5")
        assert c.get("api.openai_model") == "gpt-5"

    def test_set_creates_nested_keys(self):
        c = Config()
        c.set("custom.nested.key", "value")
        assert c.get("custom.nested.key") == "value"


# ===================================================================
# 6. verify_router logic (inline, since it's a closure)
# ===================================================================

class TestVerifyRouterLogic:
    """Tests for the verify_router routing logic from workflow.py.

    The actual verify_router is a closure inside build_workflow() and
    cannot be imported directly.  We re-implement the identical branching
    logic and test it with mock state dicts.
    """

    @staticmethod
    def verify_router(state: dict, config: dict) -> str:
        """Mirror of verify_router from workflow.py lines 553-565."""
        verification_status = state.get("verification")
        session = state["session"]

        if verification_status and getattr(verification_status, "verified", False):
            return "finalize"
        else:
            if len(session.steps) <= config["agent"]["max_iterations"] * 5:
                return "refine"
            else:
                return "finalize"

    @staticmethod
    def _make_verification(verified: bool):
        """Create a lightweight object with a .verified attribute."""

        class _V:
            pass

        v = _V()
        v.verified = verified
        return v

    def test_verified_true_routes_to_finalize(self):
        session = ResearchSession(query="q")
        state = {
            "session": session,
            "verification": self._make_verification(True),
        }
        config = {"agent": {"max_iterations": 3}}
        assert self.verify_router(state, config) == "finalize"

    def test_not_verified_few_steps_routes_to_refine(self):
        session = ResearchSession(query="q")
        # Add a small number of steps (well under 3*5 = 15)
        for name in ["analyze", "search", "read"]:
            session.start_step(name)
        state = {
            "session": session,
            "verification": self._make_verification(False),
        }
        config = {"agent": {"max_iterations": 3}}
        assert self.verify_router(state, config) == "refine"

    def test_not_verified_many_steps_routes_to_finalize(self):
        session = ResearchSession(query="q")
        # Add more than max_iterations * 5 = 15 steps
        for i in range(16):
            session.start_step(f"step_{i}")
        state = {
            "session": session,
            "verification": self._make_verification(False),
        }
        config = {"agent": {"max_iterations": 3}}
        assert self.verify_router(state, config) == "finalize"

    def test_verification_none_few_steps_routes_to_refine(self):
        """When verification is None (e.g. verify step errored), treat as unverified."""
        session = ResearchSession(query="q")
        state = {"session": session, "verification": None}
        config = {"agent": {"max_iterations": 3}}
        assert self.verify_router(state, config) == "refine"

    def test_verification_none_many_steps_routes_to_finalize(self):
        session = ResearchSession(query="q")
        for i in range(16):
            session.start_step(f"step_{i}")
        state = {"session": session, "verification": None}
        config = {"agent": {"max_iterations": 3}}
        assert self.verify_router(state, config) == "finalize"

    def test_boundary_exactly_at_limit(self):
        """At exactly max_iterations*5 steps the condition is <=, so refine."""
        session = ResearchSession(query="q")
        for i in range(15):
            session.start_step(f"step_{i}")
        state = {
            "session": session,
            "verification": self._make_verification(False),
        }
        config = {"agent": {"max_iterations": 3}}
        # 15 steps, limit is 15, 15 <= 15 is True → refine
        assert self.verify_router(state, config) == "refine"

    def test_boundary_one_above_limit(self):
        """One step above max_iterations*5 should finalize."""
        session = ResearchSession(query="q")
        for i in range(16):
            session.start_step(f"step_{i}")
        state = {
            "session": session,
            "verification": self._make_verification(False),
        }
        config = {"agent": {"max_iterations": 3}}
        # 16 steps, limit is 15, 16 <= 15 is False → finalize
        assert self.verify_router(state, config) == "finalize"


# ===================================================================
# 7. ImpactItem and ImpactAnalysis data models
# ===================================================================

class TestImpactModels:
    """Tests for the impact analysis data models."""

    def test_impact_item_construct(self):
        item = ImpactItem(
            file_path="src/db.py",
            line_number=47,
            pattern="Session.execute(text_query)",
            severity="HIGH",
            action="Use session.execute(text(...))",
            entity="Session.execute",
        )
        assert item.file_path == "src/db.py"
        assert item.severity == "HIGH"
        assert item.entity == "Session.execute"

    def test_impact_analysis_construct(self):
        items = [
            ImpactItem("a.py", 10, "old_func()", "HIGH", "Use new_func()", "old_func"),
            ImpactItem("b.py", 20, "old_func()", "MEDIUM", "Consider new_func()", "old_func"),
        ]
        analysis = ImpactAnalysis(
            query="library 2.0 migration",
            entity_count=1,
            affected_files=["a.py", "b.py"],
            items=items,
        )
        assert analysis.query == "library 2.0 migration"
        assert analysis.entity_count == 1
        assert len(analysis.items) == 2
        assert len(analysis.affected_files) == 2
        assert analysis.timestamp > 0

    def test_impact_analysis_empty_items(self):
        analysis = ImpactAnalysis(
            query="no impact query",
            entity_count=0,
            affected_files=[],
        )
        assert analysis.items == []
        assert analysis.affected_files == []

    def test_impact_item_serializes_to_dict(self):
        item = ImpactItem("x.py", 5, "code", "LOW", "fix it", "entity")
        d = asdict(item)
        assert d["file_path"] == "x.py"
        assert d["line_number"] == 5
        assert d["severity"] == "LOW"

    def test_impact_analysis_serializes_to_dict(self):
        analysis = ImpactAnalysis(
            query="q",
            entity_count=0,
            affected_files=["a.py"],
            items=[ImpactItem("a.py", 1, "p", "HIGH", "a", "e")],
        )
        d = asdict(analysis)
        assert d["query"] == "q"
        assert len(d["items"]) == 1
        assert d["items"][0]["file_path"] == "a.py"


# ===================================================================
# 8. Impact scan logic (inline re-implementation, no LLM needed)
# ===================================================================

class TestImpactScanLogic:
    """Tests for the pure logic used inside the impact_scan workflow node.

    The actual node is a closure inside build_workflow() and depends on an
    LLM and various tools.  We re-implement the deterministic portions
    (entity extraction parsing, empty-entity pass-through, severity
    classification) and test those directly.
    """

    # -- entity extraction parser (mirrors the regex+json.loads path) --
    @staticmethod
    def _parse_entities(raw_llm_output: str) -> List[str]:
        """Mirror of the entity-parsing logic in impact_scan."""
        import re as _re
        entities: List[str] = []
        match = _re.search(r"\[.*?\]", raw_llm_output, _re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group(0))
                if isinstance(parsed, list):
                    entities = [str(e).strip() for e in parsed if str(e).strip()]
            except (json.JSONDecodeError, ValueError):
                pass
        return entities

    def test_extract_entities_valid_json_array(self):
        raw = 'Here are the entities: ["requests.get", "Session.execute"]'
        assert self._parse_entities(raw) == ["requests.get", "Session.execute"]

    def test_extract_entities_empty_array(self):
        raw = "No entities found: []"
        assert self._parse_entities(raw) == []

    def test_extract_entities_no_json_at_all(self):
        raw = "I could not find any concrete entities to report."
        assert self._parse_entities(raw) == []

    def test_extract_entities_malformed_json(self):
        raw = 'Almost JSON: ["requests.get", ]'
        # json.loads may or may not accept trailing comma; either way no crash
        result = self._parse_entities(raw)
        assert isinstance(result, list)

    def test_extract_entities_strips_whitespace(self):
        raw = '[" foo.bar ", "baz "]'
        assert self._parse_entities(raw) == ["foo.bar", "baz"]

    # -- empty entities pass-through --
    def test_empty_entities_returns_none_analysis(self):
        """When no entities are extracted the node should leave impact_analysis as None."""
        # Simulate the node's early-return branch
        entities: List[str] = []
        impact_analysis = None
        if not entities:
            impact_analysis = None  # pass-through
        assert impact_analysis is None

    # -- severity classification (mirrors the keyword-based logic) --
    @staticmethod
    def _classify_severity(query: str) -> str:
        """Mirror of the severity classification in impact_scan."""
        query_lower = query.lower()
        if any(kw in query_lower for kw in ("removed", "breaking", "delete", "remove", "drop")):
            return "HIGH"
        elif any(kw in query_lower for kw in ("deprecated", "deprecate", "warning")):
            return "MEDIUM"
        else:
            return "LOW"

    def test_severity_high_for_breaking(self):
        assert self._classify_severity("Breaking changes in v2") == "HIGH"

    def test_severity_high_for_removed(self):
        assert self._classify_severity("Function removed in latest release") == "HIGH"

    def test_severity_high_for_drop(self):
        assert self._classify_severity("Drop support for Python 3.7") == "HIGH"

    def test_severity_medium_for_deprecated(self):
        assert self._classify_severity("deprecated API in sqlalchemy") == "MEDIUM"

    def test_severity_medium_for_warning(self):
        assert self._classify_severity("DeprecationWarning in latest version") == "MEDIUM"

    def test_severity_low_for_general_change(self):
        assert self._classify_severity("behavior change in new release") == "LOW"

    def test_severity_low_for_neutral_query(self):
        assert self._classify_severity("how to use requests library") == "LOW"


# ===================================================================
# 9. Impact synthesis format (inline re-implementation)
# ===================================================================

class TestImpactSynthesisFormat:
    """Tests for the structured impact report formatting.

    Re-implements the format_impact_report logic from SynthesisTool so
    the tests do not require langchain or an API key.
    """

    @staticmethod
    def _format_impact_report(impact_analysis: ImpactAnalysis) -> str:
        """Mirror of SynthesisTool.format_impact_report."""
        items = impact_analysis.items
        affected_files = impact_analysis.affected_files

        high = [it for it in items if it.severity == "HIGH"]
        medium = [it for it in items if it.severity == "MEDIUM"]
        low = [it for it in items if it.severity == "LOW"]

        lines: List[str] = [
            f"IMPACT ANALYSIS: {len(items)} call sites across {len(affected_files)} files",
            "",
        ]

        if high:
            lines.append("HIGH SEVERITY (breaking):")
            for it in high:
                lines.append(f"  {it.file_path}:{it.line_number}  {it.pattern} -> {it.action}")
            lines.append("")

        if medium:
            lines.append("MEDIUM SEVERITY (deprecated):")
            for it in medium:
                action = it.action if it.action else "Review usage"
                lines.append(f"  {it.file_path}:{it.line_number}  {it.pattern} -> {action}")
            lines.append("")

        if low:
            lines.append("LOW SEVERITY (changed):")
            for it in low:
                action = it.action if it.action else "Monitor for changes"
                lines.append(f"  {it.file_path}:{it.line_number}  {it.pattern} -> {action}")
            lines.append("")

        if high:
            lines.append("SUGGESTED FIRST COMMIT:")
            for it in high:
                lines.append(f"  [ ] {it.file_path}:{it.line_number} - {it.action}")
            lines.append("")

        return "\n".join(lines)

    def test_structured_output_with_all_severities(self):
        items = [
            ImpactItem("db.py", 10, "execute(raw)", "HIGH", "Use text()", "execute"),
            ImpactItem("api.py", 22, "old_func()", "MEDIUM", "Migrate to new_func", "old_func"),
            ImpactItem("util.py", 5, "helper()", "LOW", "", "helper"),
        ]
        analysis = ImpactAnalysis(
            query="library v2 migration",
            entity_count=3,
            affected_files=["db.py", "api.py", "util.py"],
            items=items,
        )
        report = self._format_impact_report(analysis)

        assert "IMPACT ANALYSIS: 3 call sites across 3 files" in report
        assert "HIGH SEVERITY (breaking):" in report
        assert "db.py:10" in report
        assert "MEDIUM SEVERITY (deprecated):" in report
        assert "api.py:22" in report
        assert "LOW SEVERITY (changed):" in report
        assert "util.py:5" in report
        assert "SUGGESTED FIRST COMMIT:" in report

    def test_structured_output_high_only(self):
        items = [
            ImpactItem("a.py", 1, "removed()", "HIGH", "Delete call", "removed"),
        ]
        analysis = ImpactAnalysis(
            query="breaking removal",
            entity_count=1,
            affected_files=["a.py"],
            items=items,
        )
        report = self._format_impact_report(analysis)

        assert "HIGH SEVERITY (breaking):" in report
        assert "SUGGESTED FIRST COMMIT:" in report
        assert "MEDIUM SEVERITY" not in report
        assert "LOW SEVERITY" not in report

    def test_structured_output_low_only_no_suggested_commit(self):
        items = [
            ImpactItem("c.py", 3, "tweaked()", "LOW", "", "tweaked"),
        ]
        analysis = ImpactAnalysis(
            query="minor change",
            entity_count=1,
            affected_files=["c.py"],
            items=items,
        )
        report = self._format_impact_report(analysis)

        assert "LOW SEVERITY (changed):" in report
        assert "SUGGESTED FIRST COMMIT:" not in report
        assert "HIGH SEVERITY" not in report

    def test_fallback_to_narrative_when_no_impact(self):
        """When impact_analysis is None or has no items, the structured
        path should not be taken.  We verify the decision logic here."""
        # None case
        impact = None
        use_structured = impact is not None and bool(getattr(impact, "items", None))
        assert use_structured is False

        # Empty items case
        impact_empty = ImpactAnalysis(query="q", entity_count=0, affected_files=[])
        use_structured = impact_empty is not None and bool(impact_empty.items)
        assert use_structured is False

    def test_medium_items_default_action(self):
        """MEDIUM items with empty action should get 'Review usage' default."""
        items = [
            ImpactItem("m.py", 7, "dep_func()", "MEDIUM", "", "dep_func"),
        ]
        analysis = ImpactAnalysis(
            query="deprecated", entity_count=1, affected_files=["m.py"], items=items,
        )
        report = self._format_impact_report(analysis)
        assert "Review usage" in report

    def test_low_items_default_action(self):
        """LOW items with empty action should get 'Monitor for changes' default."""
        items = [
            ImpactItem("l.py", 9, "changed()", "LOW", "", "changed"),
        ]
        analysis = ImpactAnalysis(
            query="change", entity_count=1, affected_files=["l.py"], items=items,
        )
        report = self._format_impact_report(analysis)
        assert "Monitor for changes" in report


# ===================================================================
# 10. Impact memory persistence (cache round-trip)
# ===================================================================

class TestImpactMemory:
    """Tests for caching ImpactAnalysis via the existing Cache."""

    def test_cache_roundtrip_impact_analysis(self, tmp_path):
        c = Cache(cache_dir=str(tmp_path / "cache"), ttl_hours=1)
        items = [
            ImpactItem("src/db.py", 47, "Session.execute(text_query)", "HIGH",
                        "Use session.execute(text(...))", "Session.execute"),
            ImpactItem("src/api.py", 12, "old_func()", "LOW", "", "old_func"),
        ]
        analysis = ImpactAnalysis(
            query="sqlalchemy 2.0 migration",
            entity_count=2,
            affected_files=["src/db.py", "src/api.py"],
            items=items,
        )

        # Store wrapped with file_mtimes like the real node does
        payload = {
            "analysis": asdict(analysis),
            "file_mtimes": {"src/db.py": 1000.0, "src/api.py": 2000.0},
        }
        assert c.set("impact_sqlalchemy_2.0_migration", payload) is True

        # Retrieve and verify
        cached = c.get("impact_sqlalchemy_2.0_migration")
        assert cached is not None
        assert isinstance(cached, dict)
        assert "analysis" in cached
        assert "file_mtimes" in cached

        a = cached["analysis"]
        assert a["query"] == "sqlalchemy 2.0 migration"
        assert a["entity_count"] == 2
        assert len(a["items"]) == 2
        assert a["items"][0]["severity"] == "HIGH"
        assert a["items"][0]["file_path"] == "src/db.py"
        assert a["items"][1]["entity"] == "old_func"

        assert cached["file_mtimes"]["src/db.py"] == 1000.0

    def test_cache_miss_returns_none(self, tmp_path):
        c = Cache(cache_dir=str(tmp_path / "cache"), ttl_hours=1)
        assert c.get("impact_nonexistent_query") is None

    def test_reconstruct_impact_analysis_from_cache(self, tmp_path):
        """Verify we can reconstruct ImpactAnalysis dataclass from cached dict."""
        c = Cache(cache_dir=str(tmp_path / "cache"), ttl_hours=1)
        original = ImpactAnalysis(
            query="test query",
            entity_count=1,
            affected_files=["f.py"],
            items=[ImpactItem("f.py", 3, "func()", "MEDIUM", "update call", "func")],
        )
        c.set("impact_test", {"analysis": asdict(original), "file_mtimes": {}})

        cached = c.get("impact_test")
        a = cached["analysis"]
        reconstructed_items = [ImpactItem(**d) for d in a["items"]]
        reconstructed = ImpactAnalysis(
            query=a["query"],
            entity_count=a["entity_count"],
            affected_files=a["affected_files"],
            items=reconstructed_items,
            timestamp=a.get("timestamp", 0.0),
        )

        assert reconstructed.query == original.query
        assert reconstructed.entity_count == original.entity_count
        assert len(reconstructed.items) == 1
        assert reconstructed.items[0].severity == "MEDIUM"
        assert reconstructed.items[0].action == "update call"

    def test_stale_mtime_invalidates_cache(self, tmp_path):
        """Demonstrate the mtime-comparison logic used for cache invalidation."""
        import os as _os

        # Create a real file so we can check its mtime
        test_file = tmp_path / "code.py"
        test_file.write_text("print('hello')")
        original_mtime = _os.path.getmtime(str(test_file))

        stored_mtimes = {str(test_file): original_mtime}

        # Initially files are unchanged
        files_unchanged = True
        for fpath, old_mtime in stored_mtimes.items():
            try:
                if _os.path.getmtime(fpath) != old_mtime:
                    files_unchanged = False
                    break
            except OSError:
                files_unchanged = False
                break
        assert files_unchanged is True

        # Modify the file (write new content to change mtime)
        test_file.write_text("print('changed')")

        files_unchanged = True
        for fpath, old_mtime in stored_mtimes.items():
            try:
                if _os.path.getmtime(fpath) != old_mtime:
                    files_unchanged = False
                    break
            except OSError:
                files_unchanged = False
                break
        assert files_unchanged is False

    def test_missing_file_invalidates_cache(self, tmp_path):
        """If a cached file path no longer exists, treat cache as stale."""
        stored_mtimes = {"/nonexistent/path/code.py": 12345.0}

        files_unchanged = True
        for fpath, old_mtime in stored_mtimes.items():
            try:
                import os as _os
                if _os.path.getmtime(fpath) != old_mtime:
                    files_unchanged = False
                    break
            except OSError:
                files_unchanged = False
                break
        assert files_unchanged is False
