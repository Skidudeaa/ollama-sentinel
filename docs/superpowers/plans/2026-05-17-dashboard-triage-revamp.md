# Dashboard Triage-Console Visual Overhaul Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn `ollama-sentinel dashboard`'s v2 Rich TUI into a triage console — vitals strip, bold severity banner, dominant blended-ranked Patterns list, thin Reviews rail — with zero data-layer or input-model changes.

**Architecture:** Additive-first. Tasks 1-6 add pure helpers (no deletions, full suite stays green). Tasks 7-8 rewire the two v2 entry points (`render_layout` v2 branch + the `_build_layout` closure in `run_dashboard`). Task 9 deletes the now-dead superseded helpers and migrates their tests in one consistent step. Legacy two-panel path (`config_path == ""`) is never touched and is regression-locked.

**Tech Stack:** Python 3.10+, Rich (`Panel`/`Table`/`Layout`), pytest (`asyncio_mode=auto`).

**Spec:** `docs/superpowers/specs/2026-05-17-dashboard-triage-revamp-design.md`
**Branch:** `feat/dashboard-triage-revamp` (already created off `master`).

---

## Ordering invariant (why this sequence)

`_reviews_panel` is used by the **legacy** `render_layout` branch (line
419) — it must **never** be deleted. `_header_panel_v2`, `_overview_panel`,
and `_reviews_panel_interactive` are v2-only and become dead **only after
both** Tasks 7 and 8 rewire their callers — so they are deleted exactly
once, in Task 9, together with their test/import migration. Consequence:
every task ends with the **entire** suite green (`pytest tests/ -q`), not
a scoped subset. No task leaves a dangling reference to a deleted symbol.

## Test mechanism (confirmed, used by every task)

`tests/test_dashboard.py` asserts on Rich object attributes —
`panel.title`, `panel.border_style`, and `layout["region"]` indexing.
For content assertions add this helper **once**, in Task 1 Step 1,
directly below the existing `_touch` function (line 32):

```python
def _render(renderable, width: int = 120) -> str:
    """Render a Rich renderable to plain text for content assertions."""
    from rich.console import Console
    c = Console(width=width, record=True, file=open(os.devnull, "w"))
    c.print(renderable)
    return c.export_text()
```

(`os` is already imported in the test file, line 3.)

---

## File structure

| File | Responsibility | Change |
|------|----------------|--------|
| `ollama_sentinel/dashboard.py` | v2 render path + pure helpers | add `_SEVERITY_WEIGHT`/`blended_rank`/`_vitals_strip`/`_severity_banner`/`_reviews_rail`; revise `_SEVERITY_STYLE` + `_patterns_panel*` single-line; rewire `render_layout` v2 + `_build_layout` v2; **T9** deletes dead `_header_panel_v2`/`_overview_panel`/`_reviews_panel_interactive`. `_reviews_panel`/`_violations_panel`/legacy path untouched. |
| `tests/test_dashboard.py` | unit + structural + regression | add `_render`; new tests per task; **T9** migrates the import block + superseded-panel tests |

---

## Task 1: `_SEVERITY_WEIGHT` + `blended_rank` (pure)

**Files:**
- Modify: `ollama_sentinel/dashboard.py` (add after `_STATUS_STYLE`, line 47)
- Test: `tests/test_dashboard.py`

- [ ] **Step 1: Add the `_render` helper**

In `tests/test_dashboard.py`, immediately after the `_touch` function
(ends line 32, before `class TestRecentReviews`), insert the `_render`
helper exactly as shown in "Test mechanism" above.

- [ ] **Step 2: Write failing tests**

Append to `tests/test_dashboard.py`:

```python
class TestBlendedRank:
    def _vr(self, sev, count, fp="f.py", line=1):
        return ViolationRow(count=count, severity=sev, category="bug",
                             file_path=fp, line_start=line, line_end=line,
                             description="d")

    def test_severity_weight_ordering_invariant(self):
        from ollama_sentinel.dashboard import _SEVERITY_WEIGHT
        w = _SEVERITY_WEIGHT
        assert w["critical"] > w["high"] > w["medium"] > w["low"]
        assert w["critical"] > 7 * w["low"]   # one CRIT outranks 7 LOW

    def test_blended_orders_by_weight_times_count(self):
        from ollama_sentinel.dashboard import blended_rank
        rows = [
            self._vr("medium", 15),   # 2*15 = 30
            self._vr("high", 11),     # 4*11 = 44
            self._vr("critical", 4),  # 8*4  = 32
            self._vr("low", 50),      # 1*50 = 50
        ]
        ranked = blended_rank(rows)
        assert [(r.severity, r.count) for r in ranked] == [
            ("low", 50), ("high", 11), ("critical", 4), ("medium", 15)]

    def test_tiebreak_count_then_filepath(self):
        from ollama_sentinel.dashboard import blended_rank
        a = self._vr("high", 5, fp="z.py")   # 20
        b = self._vr("high", 5, fp="a.py")   # 20 -> file asc
        c = self._vr("high", 9, fp="m.py")   # 36
        ranked = blended_rank([a, b, c])
        assert [r.file_path for r in ranked] == ["m.py", "a.py", "z.py"]

    def test_unknown_severity_weight_zero_sorts_last(self):
        from ollama_sentinel.dashboard import blended_rank
        good = self._vr("low", 1)
        bad = self._vr("bogus", 999)
        assert blended_rank([bad, good]) == [good, bad]

    def test_empty_returns_empty(self):
        from ollama_sentinel.dashboard import blended_rank
        assert blended_rank([]) == []
```

- [ ] **Step 3: Run, verify FAIL**

Run: `python -m pytest tests/test_dashboard.py::TestBlendedRank -v`
Expected: FAIL — `cannot import name '_SEVERITY_WEIGHT'` / `blended_rank`.

- [ ] **Step 4: Implement**

In `ollama_sentinel/dashboard.py`, after the `_STATUS_STYLE` dict (line
47) and before the `# ---` separator (line 49), add:

```python
_SEVERITY_WEIGHT = {
    "critical": 8,
    "high": 4,
    "medium": 2,
    "low": 1,
}


def blended_rank(rows: "List[ViolationRow]") -> "List[ViolationRow]":
    """Order recurring findings by triage urgency.

    Sort key: ``severity_weight * count`` descending; unknown severities
    weigh 0 (sort last). Ties: count desc, then file_path asc. Pure and
    stable; never raises on bad input.
    """
    def key(r: "ViolationRow"):
        weight = _SEVERITY_WEIGHT.get(r.severity.lower(), 0)
        return (-(weight * r.count), -r.count, r.file_path)

    return sorted(rows, key=key)
```

- [ ] **Step 5: Run full suite, verify GREEN**

Run: `python -m pytest tests/test_dashboard.py -q`
Expected: all PASS (pure addition; `TestBlendedRank` green, nothing else
affected).

- [ ] **Step 6: Commit**

```bash
git add ollama_sentinel/dashboard.py tests/test_dashboard.py
git commit -m "feat(dashboard): add blended severity*recurrence ranking"
```

---

## Task 2: Revise `_SEVERITY_STYLE` palette

**Files:**
- Modify: `ollama_sentinel/dashboard.py:35-40`
- Test: `tests/test_dashboard.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_dashboard.py`:

```python
class TestSeverityPalette:
    def test_palette_is_bold_saturated_and_distinct(self):
        from ollama_sentinel.dashboard import _SEVERITY_STYLE
        s = _SEVERITY_STYLE
        assert s["critical"] == "bold red"
        assert s["high"] == "bold yellow"
        assert s["medium"] == "cyan"
        assert s["low"] == "dim"
        assert len(set(s.values())) == 4
```

- [ ] **Step 2: Run, verify FAIL**

Run: `python -m pytest tests/test_dashboard.py::TestSeverityPalette -v`
Expected: FAIL — `s["high"]` is `"red"`, `s["medium"]` is `"yellow"`.

- [ ] **Step 3: Implement**

In `ollama_sentinel/dashboard.py`, replace the `_SEVERITY_STYLE` dict
(lines 35-40):

```python
_SEVERITY_STYLE = {
    "critical": "bold red",
    "high": "red",
    "medium": "yellow",
    "low": "dim",
}
```

with:

```python
_SEVERITY_STYLE = {
    "critical": "bold red",
    "high": "bold yellow",
    "medium": "cyan",
    "low": "dim",
}
```

- [ ] **Step 4: Run full suite, verify GREEN**

Run: `python -m pytest tests/test_dashboard.py -q`
Expected: all PASS — no other test pins exact palette values (verified:
only `ViolationRow(severity=...)` construction references severities,
never `_SEVERITY_STYLE` values).

- [ ] **Step 5: Commit**

```bash
git add ollama_sentinel/dashboard.py tests/test_dashboard.py
git commit -m "feat(dashboard): bold saturated severity palette"
```

---

## Task 3: Add `_vitals_strip` (pure addition)

**Files:**
- Modify: `ollama_sentinel/dashboard.py` (add immediately after `_header_panel_v2`, ~line 272 — do NOT remove `_header_panel_v2`; it is still wired until Task 9)
- Test: `tests/test_dashboard.py`

- [ ] **Step 1: Write failing test**

Append to class `TestControlCenterPanels` in `tests/test_dashboard.py`:

```python
    def test_vitals_strip_renders(self):
        from ollama_sentinel.dashboard import _vitals_strip
        stats = OverviewStats(
            total_reviews=5, newest_review_age_s=30.0, total_unresolved=8,
            config_path="test.yaml", model_name="gemma3",
            watch_dir="/code", db_exists=True,
        )
        panel = _vitals_strip(stats, time.time())
        text = _render(panel)
        assert "gemma3" in text
        assert "Active" in text                 # age 30s -> Active
        assert panel.border_style == "bold cyan"

    def test_vitals_strip_handles_empty(self):
        from ollama_sentinel.dashboard import _vitals_strip
        stats = OverviewStats(total_reviews=0, newest_review_age_s=None,
                              total_unresolved=0)
        panel = _vitals_strip(stats, time.time())   # must not raise
        assert "unknown" in _render(panel)
```

- [ ] **Step 2: Run, verify FAIL**

Run: `python -m pytest "tests/test_dashboard.py::TestControlCenterPanels" -k vitals -v`
Expected: FAIL — `cannot import name '_vitals_strip'`.

- [ ] **Step 3: Implement (add only)**

In `ollama_sentinel/dashboard.py`, directly after the `_header_panel_v2`
function (after line 271, before `watcher_status_from_age`), add:

```python
def _vitals_strip(stats: OverviewStats, now: float) -> Panel:
    """One-line vitals: status dot, model, open count, update clock.

    Triage replacement for the old three-line v2 header. Status dot
    color tracks watcher staleness. Never raises on empty stats.
    """
    ts = _dt.datetime.fromtimestamp(now).strftime("%H:%M:%S")
    status_label, status_key = watcher_status_from_age(stats.newest_review_age_s)
    status_style = _STATUS_STYLE.get(status_key, "dim")
    model = stats.model_name or "unknown"
    if stats.db_exists:
        db_info = f"[white]{stats.total_unresolved}[/] open"
    else:
        db_info = "[dim]no DB[/]"
    body = (
        f"[{status_style}]●[/] [{status_style}]{status_label}[/]"
        f"  [dim]│[/]  [white]{model}[/]"
        f"  [dim]│[/]  {db_info}"
        f"  [dim]│[/]  [dim]rev[/] [white]{ts}[/]"
    )
    return Panel(Text.from_markup(body), border_style="bold cyan",
                 padding=(0, 1))
```

`watcher_status_from_age` is defined at line 274 (module scope) — fine
to call from a function defined above it.

- [ ] **Step 4: Run full suite, verify GREEN**

Run: `python -m pytest tests/test_dashboard.py -q`
Expected: all PASS — pure addition; old `test_header_v2_renders` still
green (`_header_panel_v2` untouched).

- [ ] **Step 5: Commit**

```bash
git add ollama_sentinel/dashboard.py tests/test_dashboard.py
git commit -m "feat(dashboard): add one-line vitals strip helper"
```

---

## Task 4: Add `_severity_banner` (pure addition)

**Files:**
- Modify: `ollama_sentinel/dashboard.py` (add after `_vitals_strip`)
- Test: `tests/test_dashboard.py`

- [ ] **Step 1: Write failing test**

Append to class `TestControlCenterPanels`:

```python
    def test_severity_banner_shows_counts_and_action(self):
        from ollama_sentinel.dashboard import _severity_banner
        stats = OverviewStats(
            total_reviews=59, newest_review_age_s=30.0, total_unresolved=1230,
            severity_counts={"critical": 74, "high": 104,
                             "medium": 576, "low": 476},
            hottest_file="ErasZoneView.swift", hottest_count=239,
            db_exists=True,
        )
        text = _render(_severity_banner(stats))
        assert "74" in text and "104" in text and "576" in text and "476" in text
        assert "ErasZoneView.swift" in text and "239" in text
        assert "critical" in text.lower()       # from suggested_action

    def test_severity_banner_empty_placeholder(self):
        from ollama_sentinel.dashboard import _severity_banner
        stats = OverviewStats(total_reviews=0, newest_review_age_s=None,
                              total_unresolved=0, db_exists=False)
        assert "no findings" in _render(_severity_banner(stats)).lower()
```

- [ ] **Step 2: Run, verify FAIL**

Run: `python -m pytest "tests/test_dashboard.py::TestControlCenterPanels" -k severity_banner -v`
Expected: FAIL — `cannot import name '_severity_banner'`.

- [ ] **Step 3: Implement**

In `ollama_sentinel/dashboard.py`, immediately after `_vitals_strip`, add:

```python
def _severity_banner(stats: OverviewStats) -> Panel:
    """Bold severity scoreboard + hottest-file / next-action callout.

    Line 1: per-severity counts (descending severity), each styled by
    _SEVERITY_STYLE. Line 2: hottest file + suggested action. Empty /
    no-DB state shows a muted placeholder; never raises.
    """
    if not stats.db_exists or stats.total_unresolved == 0:
        msg = ("[dim]no findings yet — save a watched file[/]"
               if stats.total_reviews == 0
               else "[bold green]All clear — no open findings[/]")
        return Panel(Text.from_markup(msg), border_style="green",
                     padding=(0, 1))

    cells = []
    for sev in ("critical", "high", "medium", "low"):
        count = stats.severity_counts.get(sev, 0)
        style = _SEVERITY_STYLE[sev]
        cells.append(f"[{style}]{sev[:4].upper()} {count}[/]")
    line1 = "   ".join(cells)

    if stats.hottest_file:
        hot = (f"[red]🔥[/] [white]{stats.hottest_file}[/]"
               f" [dim]({stats.hottest_count})[/]")
    else:
        hot = "[dim]🔥 —[/]"
    line2 = f"{hot}   [dim]▸[/] [italic]{suggested_action(stats)}[/]"

    border = "red" if stats.severity_counts.get("critical", 0) else "yellow"
    return Panel(Text.from_markup(f"{line1}\n{line2}"),
                 border_style=border, padding=(0, 1))
```

- [ ] **Step 4: Run full suite, verify GREEN**

Run: `python -m pytest tests/test_dashboard.py -q`
Expected: all PASS (pure addition).

- [ ] **Step 5: Commit**

```bash
git add ollama_sentinel/dashboard.py tests/test_dashboard.py
git commit -m "feat(dashboard): add severity banner with hottest/action"
```

---

## Task 5: Add `_reviews_rail` (pure addition)

**Files:**
- Modify: `ollama_sentinel/dashboard.py` (add after `_severity_banner`; remove nothing — `_reviews_panel` stays for legacy, `_reviews_panel_interactive` removed in Task 9)
- Test: `tests/test_dashboard.py`

- [ ] **Step 1: Write failing test**

Append to class `TestControlCenterPanels`:

```python
    def test_reviews_rail_compact_and_selection(self):
        from ollama_sentinel.dashboard import _reviews_rail
        now = time.time()
        rows = [ReviewRow(rel_path="Sources/Vinyl/VinylAudioSourceSelector.md",
                          mtime=now - 2760),
                ReviewRow(rel_path="a/b/Left.md", mtime=now - 3000)]
        panel = _reviews_rail(rows, now, selection=0, scroll=0)
        text = _render(panel, width=40)
        assert "46m" in text                         # 2760s -> 46m ago
        assert "VinylAudioSourceSelector" in text    # basename kept
        assert panel.border_style == "blue"
        assert all(len(ln) <= 40 for ln in text.splitlines())

    def test_reviews_rail_empty(self):
        from ollama_sentinel.dashboard import _reviews_rail
        panel = _reviews_rail([], time.time(), selection=-1, scroll=0)
        assert "no reviews" in _render(panel).lower()
```

- [ ] **Step 2: Run, verify FAIL**

Run: `python -m pytest "tests/test_dashboard.py::TestControlCenterPanels" -k reviews_rail -v`
Expected: FAIL — `cannot import name '_reviews_rail'`.

- [ ] **Step 3: Implement (add only)**

In `ollama_sentinel/dashboard.py`, immediately after `_severity_banner`,
add:

```python
def _reviews_rail(
    rows: List[ReviewRow], now: float, selection: int, scroll: int,
) -> Panel:
    """Narrow Recent-Reviews rail: '{ago}  {basename}', one line each.

    Selection highlight preserved (REVIEWS panel stays focusable). Path
    is basename-biased so the rail stays readable when narrow.
    """
    if not rows:
        return Panel(Text("no reviews yet", style="dim"),
                     title="Recent", border_style="blue")
    visible = rows[scroll:scroll + 15]
    table = Table.grid(padding=(0, 1), expand=True)
    table.add_column(justify="right", no_wrap=True)
    table.add_column(no_wrap=True, overflow="ellipsis")
    for i, r in enumerate(visible):
        sel = (scroll + i) == selection
        name = r.rel_path.rsplit("/", 1)[-1]
        table.add_row(
            Text(_format_ago(r.mtime, now), style="reverse" if sel else "dim"),
            Text(name, style="reverse" if sel else ""),
        )
    count = len(rows)
    title = f"Recent ({count})"
    if scroll > 0 or scroll + 15 < count:
        title += f" [{scroll + 1}-{min(scroll + 15, count)}]"
    return Panel(table, title=title, border_style="blue")
```

- [ ] **Step 4: Run full suite, verify GREEN**

Run: `python -m pytest tests/test_dashboard.py -q`
Expected: all PASS (pure addition).

- [ ] **Step 5: Commit**

```bash
git add ollama_sentinel/dashboard.py tests/test_dashboard.py
git commit -m "feat(dashboard): add compact reviews rail helper"
```

---

## Task 6: Single-line Patterns rows

**Files:**
- Modify: `ollama_sentinel/dashboard.py` — `_patterns_panel` (line 345), `_patterns_panel_interactive` (line 810)
- Test: `tests/test_dashboard.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_dashboard.py`:

```python
class TestPatternsSingleLine:
    LONG = ("Fragile auto-collapse on timeout: the timeout collapses "
            "detailExpanded unconditionally and will incorrectly collapse "
            "a different era's detail opened during the override window.")

    def _row(self):
        return ViolationRow(count=11, severity="high", category="bug",
                            file_path="ErasZoneView.swift", line_start=183,
                            line_end=190, description=self.LONG)

    def test_interactive_row_stays_one_line(self):
        from ollama_sentinel.dashboard import _patterns_panel_interactive
        panel = _patterns_panel_interactive([self._row()], selection=-1, scroll=0)
        out = _render(panel, width=80)
        body = [l for l in out.splitlines() if "ErasZoneView.swift" in l]
        assert len(body) == 1                 # description did NOT wrap
        assert "…" in out                     # it was ellipsised

    def test_static_patterns_row_stays_one_line(self):
        from ollama_sentinel.dashboard import _patterns_panel
        panel = _patterns_panel([self._row()])
        body = [l for l in _render(panel, width=80).splitlines()
                if "ErasZoneView.swift" in l]
        assert len(body) == 1
```

- [ ] **Step 2: Run, verify FAIL**

Run: `python -m pytest tests/test_dashboard.py::TestPatternsSingleLine -v`
Expected: FAIL — description column has no `no_wrap=True`, so the long
description wraps and `ErasZoneView.swift` appears with the description
spilling to extra physical lines (the body-line / `…` assertions fail).

- [ ] **Step 3: Implement**

In `_patterns_panel`, change line 345:

```python
    table.add_column(overflow="ellipsis")
```
to:
```python
    table.add_column(no_wrap=True, overflow="ellipsis")
```

In `_patterns_panel_interactive`, change line 810:

```python
    table.add_column(overflow="ellipsis")
```
to:
```python
    table.add_column(no_wrap=True, overflow="ellipsis")
```

- [ ] **Step 4: Run full suite, verify GREEN**

Run: `python -m pytest tests/test_dashboard.py -q`
Expected: all PASS — single-line rows; full text still reachable via
Enter→DETAIL (`_detail_panel` unchanged).

- [ ] **Step 5: Commit**

```bash
git add ollama_sentinel/dashboard.py tests/test_dashboard.py
git commit -m "fix(dashboard): patterns rows stay single-line (ellipsis)"
```

---

## Task 7: Rewire `render_layout` v2 branch

**Files:**
- Modify: `ollama_sentinel/dashboard.py:425-458` (Control Center branch only — legacy branch lines 409-423 untouched)
- Modify: `tests/test_dashboard.py` — `test_new_signature_produces_control_center` (lines 385-396)
- Test: `tests/test_dashboard.py`

- [ ] **Step 1: Replace the structure test**

In `tests/test_dashboard.py`, replace `test_new_signature_produces_control_center`
(lines 385-396) with:

```python
    def test_new_signature_produces_triage_layout(self, tmp_path):
        now = time.time()
        layout = render_layout(
            str(tmp_path), tmp_path, tmp_path / "memory.db",
            [], [], now,
            config_path="test.yaml", model_name="gemma3",
            severity_counts={"high": 2},
        )
        assert layout["header"] is not None
        assert layout["banner"] is not None
        assert layout["body"]["left"] is not None
        assert layout["body"]["right"] is not None
        assert layout["footer"] is not None
```

(`test_old_signature_produces_legacy_layout` is left exactly as-is — it
is the legacy regression lock.)

- [ ] **Step 2: Run, verify FAIL**

Run: `python -m pytest tests/test_dashboard.py::TestRenderLayoutBackwardsCompat -v`
Expected: `test_new_signature_produces_triage_layout` FAILS (`KeyError:
'banner'` — old v2 tree has no banner region). Legacy test still PASSES.

- [ ] **Step 3: Implement — rebuild only the v2 branch**

In `ollama_sentinel/dashboard.py`, replace the Control Center branch of
`render_layout` (lines 425-458: from the `# Control Center layout`
comment through its `return layout`) with:

```python
    # Control Center triage layout
    stats = compute_overview(
        reviews=reviews,
        severity_counts=severity_counts or {},
        hottest=hottest,
        new_this_week=new_this_week,
        config_path=config_path,
        model_name=model_name,
        watch_dir=watch_dir,
        db_exists=db_path.exists(),
        now=now,
        research_latest=research_latest,
    )
    ranked = blended_rank(violations)

    layout = Layout()
    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="banner", size=4),
        Layout(name="body", ratio=1),
        Layout(name="footer", size=3),
    )
    layout["header"].update(_vitals_strip(stats, now))
    layout["banner"].update(_severity_banner(stats))
    layout["body"].split_row(
        Layout(name="left", ratio=3),
        Layout(name="right", ratio=1),
    )
    layout["body"]["left"].update(_patterns_panel(ranked))
    layout["body"]["right"].update(_reviews_rail(reviews, now, -1, 0))
    layout["footer"].update(_footer_panel_v2())
    return layout
```

Do **not** delete `_overview_panel`/`_header_panel_v2` here — they are
still referenced by `_build_layout` until Task 8; removed in Task 9.

- [ ] **Step 4: Run full suite, verify GREEN (incl. legacy lock)**

Run: `python -m pytest tests/test_dashboard.py -q`
Expected: all PASS — `test_new_signature_produces_triage_layout` green
AND `test_old_signature_produces_legacy_layout` green (legacy branch
and `_reviews_panel` untouched).

- [ ] **Step 5: Commit**

```bash
git add ollama_sentinel/dashboard.py tests/test_dashboard.py
git commit -m "feat(dashboard): render_layout v2 -> triage region tree"
```

---

## Task 8: Rewire the `_build_layout` closure (live path)

**Files:**
- Modify: `ollama_sentinel/dashboard.py:616-684` (Control Center section of `_build_layout` inside `run_dashboard`)
- Test: `tests/test_dashboard.py`

- [ ] **Step 1: Write failing wiring-guard test**

The closure can't be imported; guard its source (project closure-testing
pattern — its pure panels are already unit-tested in Tasks 1-6). Append:

```python
class TestBuildLayoutWiring:
    def _src(self):
        import inspect
        from ollama_sentinel.dashboard import run_dashboard
        return inspect.getsource(run_dashboard)

    def test_live_path_uses_triage_tree_and_blended_rank(self):
        src = self._src()
        assert "blended_rank(" in src
        assert 'Layout(name="banner"' in src
        assert "_vitals_strip(" in src
        assert "_severity_banner(" in src
        assert "_reviews_rail(" in src
        assert "_overview_panel(" not in src
        assert "_header_panel_v2(" not in src
        assert "_reviews_panel_interactive(" not in src

    def test_detail_mode_path_preserved(self):
        src = self._src()
        assert "Mode.DETAIL" in src and "_detail_panel(" in src
```

- [ ] **Step 2: Run, verify FAIL**

Run: `python -m pytest tests/test_dashboard.py::TestBuildLayoutWiring -v`
Expected: FAIL — current `_build_layout` still calls `_header_panel_v2`/
`_overview_panel`/`_reviews_panel_interactive`, no `blended_rank`/banner.

- [ ] **Step 3: Implement — rebuild the closure's v2 section**

In `ollama_sentinel/dashboard.py`, replace the Control Center section of
`_build_layout` (lines 616-684: from the `# Interactive Control Center
layout` comment through its final `return layout`) with:

```python
        # Interactive Control Center triage layout
        stats = compute_overview(
            reviews=reviews,
            severity_counts=data["severity_counts"],
            hottest=data["hottest"],
            new_this_week=data["new_this_week"],
            config_path=config_path,
            model_name=model_name,
            watch_dir=str(watch_dir),
            db_exists=db_path.exists(),
            now=now,
            research_latest=data["research_latest"],
        )
        ranked = blended_rank(violations)

        layout = Layout()
        layout.split_column(
            Layout(name="header", size=3),
            Layout(name="banner", size=4),
            Layout(name="body", ratio=1),
            Layout(name="footer", size=3),
        )
        layout["header"].update(_vitals_strip(stats, now))

        # Detail mode: keep banner, replace body full-width
        if state.mode == Mode.DETAIL:
            layout["banner"].update(_severity_banner(stats))
            layout["body"].update(_detail_panel(state, reviews, ranked, now))
            layout["footer"].update(_footer_interactive(state))
            return layout

        # OVERVIEW focus highlights the banner region border.
        banner_p = _severity_banner(stats)
        if state.focused_panel == PanelId.OVERVIEW:
            banner_p.border_style = "bold cyan"
        layout["banner"].update(banner_p)

        reviews_border = "bold cyan" if state.focused_panel == PanelId.REVIEWS else "blue"
        patterns_border = "bold cyan" if state.focused_panel == PanelId.PATTERNS else "magenta"

        sel_idx_p = state.selection.get(PanelId.PATTERNS, 0) if state.focused_panel == PanelId.PATTERNS else -1
        scroll_p = state.scroll_offset.get(PanelId.PATTERNS, 0)
        title_suffix = f" [filter: {state.filter_text}]" if state.filter_active else ""
        patterns_p = _patterns_panel_interactive(ranked, sel_idx_p, scroll_p, title_suffix)
        patterns_p.border_style = patterns_border

        sel_idx_r = state.selection.get(PanelId.REVIEWS, 0) if state.focused_panel == PanelId.REVIEWS else -1
        scroll_r = state.scroll_offset.get(PanelId.REVIEWS, 0)
        reviews_p = _reviews_rail(reviews, now, sel_idx_r, scroll_r)
        reviews_p.border_style = reviews_border

        layout["body"].split_row(
            Layout(name="left", ratio=3),
            Layout(name="right", ratio=1),
        )
        layout["body"]["left"].update(patterns_p)
        layout["body"]["right"].update(reviews_p)

        if interactive:
            layout["footer"].update(_footer_interactive(state))
        else:
            layout["footer"].update(_footer_panel_v2())

        return layout
```

`_detail_panel`'s signature is `(state, reviews, violations, now)`
(verified dashboard.py:833). Passing `ranked` as the violations arg
keeps DETAIL selection indices consistent with the ranked Patterns list
the user navigated. `_patterns_panel_interactive(rows, selection,
scroll, title_suffix)` and `_reviews_rail(rows, now, selection, scroll)`
match their Task 5/6 signatures.

- [ ] **Step 4: Run full suite, verify GREEN**

Run: `python -m pytest tests/test_dashboard.py -q`
Expected: all PASS — wiring guard green; `TestRunDashboard` loop tests
green (loop / `_fetch_data` / error-isolation untouched).

- [ ] **Step 5: Commit**

```bash
git add ollama_sentinel/dashboard.py tests/test_dashboard.py
git commit -m "feat(dashboard): live _build_layout -> triage tree + blended rank"
```

---

## Task 9: Delete superseded helpers + migrate their tests

After Tasks 7-8, `_header_panel_v2`, `_overview_panel`, and
`_reviews_panel_interactive` have **zero callers**. Remove them and
their now-orphaned tests in one consistent step.

**Files:**
- Modify: `ollama_sentinel/dashboard.py` — delete `_header_panel_v2`, `_overview_panel`, `_reviews_panel_interactive`
- Modify: `tests/test_dashboard.py` — import block (lines 21-24), `test_overview_panel_renders`, `test_header_v2_renders`

- [ ] **Step 1: Prove they are dead**

Run: `grep -n "_header_panel_v2\|_overview_panel\|_reviews_panel_interactive" ollama_sentinel/dashboard.py`
Expected: only the `def` lines themselves (no call sites). If any call
site remains, STOP — Task 7 or 8 is incomplete.

- [ ] **Step 2: Delete the three functions**

In `ollama_sentinel/dashboard.py` delete, in full:
- `_header_panel_v2` (the `def _header_panel_v2(...)` block)
- `_overview_panel` (the `def _overview_panel(...)` block)
- `_reviews_panel_interactive` (the `def _reviews_panel_interactive(...)` block)

Leave `_reviews_panel`, `_violations_panel`, `_header_panel`,
`_footer_panel`, `_footer_panel_v2`, `_footer_interactive`,
`_patterns_panel`, `_patterns_panel_interactive`, `_detail_panel`
untouched.

- [ ] **Step 3: Migrate the test file**

In `tests/test_dashboard.py` import block (lines 21-24), remove the
`_overview_panel,` and `_header_panel_v2,` lines (keep `_patterns_panel,`
and `_footer_panel_v2,`). Delete `test_overview_panel_renders` and
`test_header_v2_renders` from `TestControlCenterPanels` (their content
moved to `_vitals_strip`/`_severity_banner`, already covered by
`test_vitals_strip_renders` + `test_severity_banner_*`).

- [ ] **Step 4: Run full suite, verify GREEN**

Run: `python -m pytest tests/test_dashboard.py -q`
Expected: all PASS — no import error, no orphaned test, no dead code.

- [ ] **Step 5: Static surface check**

Run: `python -c "import ollama_sentinel.dashboard as d; assert hasattr(d,'blended_rank') and hasattr(d,'_vitals_strip') and hasattr(d,'_severity_banner') and hasattr(d,'_reviews_rail') and hasattr(d,'_reviews_panel') and not hasattr(d,'_header_panel_v2') and not hasattr(d,'_overview_panel') and not hasattr(d,'_reviews_panel_interactive')"`
Expected: exit 0 — new surface present, legacy `_reviews_panel` kept,
superseded v2 helpers gone.

- [ ] **Step 6: Commit**

```bash
git add ollama_sentinel/dashboard.py tests/test_dashboard.py
git commit -m "refactor(dashboard): drop superseded v2 panels + migrate tests"
```

---

## Task 10: Full suite, manual smoke, PR

- [ ] **Step 1: Whole project suite**

Run: `python -m pytest tests/ -q`
Expected: all green. Net test delta vs. master: `+~17` added (Tasks
1-8) `-2` removed (`test_overview_panel_renders`, `test_header_v2_renders`,
Task 9) `~1` replaced (`test_new_signature…` renamed). Investigate any
unexpected failure before proceeding — do not edit assertions to force
green.

- [ ] **Step 2: Manual smoke (user-run — a Rich TUI cannot be asserted headless)**

Ask the user to run, in a real terminal against a project with a
populated `.ollama_reviews/memory.db`:
`ollama-sentinel dashboard <config.yaml>`
and confirm: one-line vitals strip on top; bold severity banner with
the CRITICAL count prominent + 🔥 hottest line; Patterns list dominant
and single-line; Reviews a thin right rail; `Tab`/`j`/`k`/`Enter`/`/`
behave exactly as before; `Enter` on a Pattern shows full description in
detail. This is a checklist item, not an automated gate.

- [ ] **Step 3: Push + open PR**

```bash
git push -u origin HEAD
gh pr create --title "feat(dashboard): triage-console visual overhaul" \
  --body "Rebuilds the \`ollama-sentinel dashboard\` v2 Rich TUI as a triage console: one-line vitals strip, bold severity banner (CRITICAL prominent + 🔥 hottest/next-action), a dominant Patterns list ranked by blended severity×recurrence (CRIT8/HIGH4/MED2/LOW1, count then file tiebreak), and a thin Recent-Reviews rail. Kills the wasted whitespace and the wrapping wall-of-text — rows are single-line, full text on Enter→detail.

Zero data-layer changes, zero \`dashboard_input.py\` changes (same q·Tab·j/k·Enter·/ model; Tab still cycles OVERVIEW→REVIEWS→PATTERNS, OVERVIEW focus now highlights the banner). Legacy two-panel path (\`config_path == \"\"\`) untouched and regression-locked. Superseded v2 helpers (\`_header_panel_v2\`, \`_overview_panel\`, \`_reviews_panel_interactive\`) removed; their tests migrated to the new surface — a transparent, expected consequence of a visual overhaul.

Spec: \`docs/superpowers/specs/2026-05-17-dashboard-triage-revamp-design.md\`
Plan: \`docs/superpowers/plans/2026-05-17-dashboard-triage-revamp.md\`"
```

---

## Self-review

- **Spec coverage:** vitals strip → T3; severity banner → T4; blended
  rank 8/4/2/1 + tiebreaks → T1; one-line Patterns + Enter-detail → T6;
  bold palette → T2; thin reviews rail → T5; new region tree both v2
  entry points → T7 (pure) + T8 (closure); legacy regression-lock → T7
  S4 (and untouched by construction); superseded-helper cleanup → T9;
  manual TUI verification → T10 S2. Every spec section maps to a task. ✓
- **Ordering correctness:** `_reviews_panel` (legacy dependency) is never
  deleted. The three v2-only helpers are deleted only in T9, after both
  callers (T7, T8) are rewired — every task ends with the whole suite
  green, no dangling deleted-symbol references. The earlier draft's
  interleaved deletions (which would have broken the legacy lock at T5)
  are removed. ✓
- **Placeholder scan:** every code/test step has literal content; the
  sole manual step (T10 S2) is explicitly marked non-automatable, not a
  vague code instruction. ✓
- **Name/type consistency:** `blended_rank`, `_SEVERITY_WEIGHT`,
  `_vitals_strip`, `_severity_banner(stats)`, `_reviews_rail(rows, now,
  selection, scroll)`, `_patterns_panel_interactive(rows, selection,
  scroll, title_suffix)`, region names `header/banner/body{left,right}/
  footer`, ratio `3:1`, and reused `_detail_panel(state, reviews,
  violations, now)` are spelled identically across T1-T9 and tests. ✓
