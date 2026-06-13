"""Live Control Center for the Ollama Sentinel watcher.

Polls the reviews output directory and the ViolationDB to render a read-only
Rich Live view. Runs as a separate process from ``ollama-sentinel run`` so the
watcher's log output stays untouched.
"""
from __future__ import annotations

import asyncio
import datetime as _dt
import logging
import pathlib
import re
import time
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .violation_db import ViolationDB

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Style constants
# ---------------------------------------------------------------------------

_SEVERITY_STYLE = {
    "critical": "bold red",
    "high": "bold yellow",
    "medium": "cyan",
    "low": "dim",
}

_STATUS_STYLE = {
    "active": "bold green",
    "idle": "yellow",
    "stale": "dim red",
    "no_data": "dim",
}

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


# ---------------------------------------------------------------------------
# Pure data helpers (unit-tested)
# ---------------------------------------------------------------------------

_VERSION_SUFFIX = re.compile(r"_\d{14}$")


@dataclass
class ReviewRow:
    """A reviewed source file's latest output-file entry."""
    rel_path: str   # path relative to the reviews directory, forward-slashed
    mtime: float


@dataclass
class ViolationRow:
    """A recurring finding from ViolationDB."""
    count: int
    severity: str
    category: str
    file_path: str
    line_start: int
    line_end: int
    description: str


@dataclass
class GuardrailRow:
    """An active project guardrail for the dashboard panel."""
    name: str
    scope: str   # human-readable scope label ("security · src/*.py", "all files")
    source: str  # "manual" | "promoted"


@dataclass
class OverviewStats:
    """Aggregate system state for the Control Center overview card."""
    total_reviews: int
    newest_review_age_s: Optional[float]
    total_unresolved: int
    severity_counts: Dict[str, int] = field(default_factory=dict)
    hottest_file: Optional[str] = None
    hottest_count: int = 0
    new_this_week: int = 0
    config_path: str = ""
    model_name: str = ""
    watch_dir: str = ""
    db_exists: bool = False
    research_latest: Optional[Dict] = None


def recent_reviews(reviews_dir: pathlib.Path, limit: int) -> List[ReviewRow]:
    """Return latest review files under *reviews_dir*, sorted by mtime desc.

    Skips versioned snapshot files (stem ending in ``_YYYYMMDDHHMMSS``) — the
    dashboard only cares about the latest review per source file.
    """
    reviews_dir = pathlib.Path(reviews_dir)
    if not reviews_dir.is_dir():
        return []

    rows: list[ReviewRow] = []
    for p in reviews_dir.rglob("*.md"):
        if _VERSION_SUFFIX.search(p.stem):
            continue
        try:
            mtime = p.stat().st_mtime
        except OSError:
            continue
        rel = p.relative_to(reviews_dir).as_posix()
        rows.append(ReviewRow(rel_path=rel, mtime=mtime))

    rows.sort(key=lambda r: r.mtime, reverse=True)
    return rows[:limit]


def top_violations(db: ViolationDB, min_count: int, limit: int) -> List[ViolationRow]:
    """Return recurring findings from the DB as ``ViolationRow`` objects."""
    raw = db.get_recurring(min_count=min_count, limit=limit)
    return [
        ViolationRow(
            count=r["occurrence_count"],
            severity=r["severity"],
            category=r["category"],
            file_path=r["file_path"],
            line_start=r["line_start"],
            line_end=r["line_end"],
            description=r["description"],
        )
        for r in raw
    ]


def _format_guardrail_scope(category: Optional[str], path_glob: Optional[str]) -> str:
    """Human-readable scope label for a guardrail row."""
    bits = [b for b in (category, path_glob) if b]
    return " · ".join(bits) if bits else "all files"


def active_guardrails(db: ViolationDB) -> List[GuardrailRow]:
    """Return active guardrails from the DB as ``GuardrailRow`` objects.

    Read-only; mirrors ``top_violations``. The caller's per-source try/except
    isolates a failure here from the rest of the dashboard.
    """
    return [
        GuardrailRow(
            name=g["name"],
            scope=_format_guardrail_scope(g.get("scope_category"), g.get("scope_path_glob")),
            source=g.get("source", "manual"),
        )
        for g in db.get_active_guardrails()
    ]


def watcher_status(reviews: List[ReviewRow], now: float) -> tuple:
    """Infer watcher liveness from review output recency.

    Returns (label, style_key) where style_key indexes into _STATUS_STYLE.
    """
    if not reviews:
        return ("No Data", "no_data")
    age = now - reviews[0].mtime
    if age < 60:
        return ("Active", "active")
    if age < 300:
        return ("Idle", "idle")
    return ("Stale", "stale")


def compute_overview(
    reviews: List[ReviewRow],
    severity_counts: Dict[str, int],
    hottest: Optional[tuple],
    new_this_week: int,
    config_path: str,
    model_name: str,
    watch_dir: str,
    db_exists: bool,
    now: float,
    research_latest: Optional[Dict] = None,
) -> OverviewStats:
    """Compute aggregate overview stats from pre-fetched data."""
    newest_age = (now - reviews[0].mtime) if reviews else None
    total_unresolved = sum(severity_counts.values())
    return OverviewStats(
        total_reviews=len(reviews),
        newest_review_age_s=newest_age,
        total_unresolved=total_unresolved,
        severity_counts=severity_counts,
        hottest_file=hottest[0] if hottest else None,
        hottest_count=hottest[1] if hottest else 0,
        new_this_week=new_this_week,
        config_path=config_path,
        model_name=model_name,
        watch_dir=watch_dir,
        db_exists=db_exists,
        research_latest=research_latest,
    )


def suggested_action(stats: OverviewStats) -> str:
    """Return a single actionable sentence based on system state."""
    crit = stats.severity_counts.get("critical", 0)
    high = stats.severity_counts.get("high", 0)

    if stats.total_reviews == 0:
        return "Save a watched file to generate your first review"
    if crit > 0:
        return f"Resolve {crit} critical finding{'s' if crit > 1 else ''}"
    if high > 0:
        return f"Address {high} high-severity finding{'s' if high > 1 else ''}"
    if stats.total_unresolved > 0:
        return f"{stats.total_unresolved} open finding{'s' if stats.total_unresolved > 1 else ''} across your codebase"
    return "All clear — no open findings"


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _format_ago(mtime: float, now: float) -> str:
    delta = max(0, int(now - mtime))
    if delta < 60:
        return f"{delta}s ago"
    if delta < 3600:
        return f"{delta // 60}m ago"
    if delta < 86400:
        return f"{delta // 3600}h ago"
    return f"{delta // 86400}d ago"


def _reviews_panel(rows: List[ReviewRow], now: float) -> Panel:
    if not rows:
        return Panel(Text("no reviews yet — save a watched file", style="dim"),
                     title="Recent Reviews", border_style="blue")
    table = Table.grid(padding=(0, 1), expand=True)
    table.add_column(justify="right", style="dim", no_wrap=True)
    table.add_column(no_wrap=True, overflow="ellipsis")
    for r in rows:
        table.add_row(_format_ago(r.mtime, now), r.rel_path)
    return Panel(table, title=f"Recent Reviews ({len(rows)})", border_style="blue")


def _violations_panel(rows: List[ViolationRow]) -> Panel:
    if not rows:
        return Panel(Text("no recurring violations yet", style="dim"),
                     title="Top Recurring", border_style="magenta")
    table = Table.grid(padding=(0, 1), expand=True)
    table.add_column(justify="right", style="bold red", no_wrap=True)
    table.add_column(no_wrap=True)
    table.add_column(no_wrap=True)
    table.add_column(overflow="ellipsis")
    for r in rows:
        sev_style = _SEVERITY_STYLE.get(r.severity.lower(), "white")
        table.add_row(
            f"{r.count}x",
            Text(r.severity, style=sev_style),
            r.category,
            f"{r.file_path}:{r.line_start} — {r.description}",
        )
    return Panel(table, title=f"Top Recurring ({len(rows)})", border_style="magenta")


# ---------------------------------------------------------------------------
# Control Center v2 panels
# ---------------------------------------------------------------------------

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

    _ABBR = {"critical": "CRIT", "high": "HIGH", "medium": "MED", "low": "LOW"}
    cells = []
    for sev in ("critical", "high", "medium", "low"):
        count = stats.severity_counts.get(sev, 0)
        style = _SEVERITY_STYLE[sev]
        cells.append(f"[{style}]{_ABBR[sev]} {count}[/]")
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


def watcher_status_from_age(age_s: Optional[float]) -> tuple:
    """Derive watcher status from the newest review's age in seconds."""
    if age_s is None:
        return ("No Data", "no_data")
    if age_s < 60:
        return ("Active", "active")
    if age_s < 300:
        return ("Idle", "idle")
    return ("Stale", "stale")


def _format_pattern_row(r: ViolationRow, *, selected: bool = False) -> Text:
    """Build a single Patterns row as a fixed-width Text, ellipsised by Rich.

    Bypasses Rich's column auto-sizer (which silently drops min_width columns
    when total constraints don't fit) by manually padding count/severity/
    category to fixed widths inside one Text. The description is appended and
    Rich's no_wrap=True/overflow="ellipsis" trims it cleanly. The visible
    layout is predictable at every terminal width.
    """
    sev_style = "reverse" if selected else _SEVERITY_STYLE.get(r.severity.lower(), "white")
    base_style = "reverse" if selected else ""
    bold_style = "reverse" if selected else "bold"
    t = Text(no_wrap=True, overflow="ellipsis")
    t.append(f"{r.count}x".rjust(5), style=bold_style)          # 5 chars (fits "9999x")
    t.append(" ")
    t.append(f"{r.severity:<8}", style=sev_style)               # 8 chars (fits "critical")
    t.append(" ")
    t.append(f"{r.category:<11}", style=base_style)             # 11 chars (fits "performance")
    t.append(" ")
    t.append(f"{r.file_path}:{r.line_start} — {r.description}",
             style=base_style)
    return t


def _patterns_panel(rows: List[ViolationRow]) -> Panel:
    """Patterns panel — renamed from 'Top Recurring' for clearer mental model."""
    if not rows:
        return Panel(
            Text("no patterns detected yet — run some reviews first", style="dim"),
            title="Patterns", border_style="magenta",
        )
    table = Table.grid(padding=(0, 0), expand=True)
    table.add_column(no_wrap=True, overflow="ellipsis")
    for r in rows:
        table.add_row(_format_pattern_row(r))
    return Panel(table, title=f"Patterns ({len(rows)})", border_style="magenta")


def _guardrails_panel(rows: List[GuardrailRow]) -> Panel:
    """Active-guardrails panel: name + scope + source, one row each.

    Read-only and non-focusable (curation happens via the CLI). Empty state
    degrades to a muted hint; never raises on bad input.
    """
    if not rows:
        return Panel(
            Text("no guardrails — author one with `guardrail add`", style="dim"),
            title="Guardrails", border_style="green",
        )
    table = Table.grid(padding=(0, 1), expand=True)
    table.add_column(no_wrap=True, overflow="ellipsis")   # name
    table.add_column(no_wrap=True, overflow="ellipsis", style="dim")  # scope
    table.add_column(no_wrap=True, justify="right", style="dim")      # source
    for r in rows:
        src = "auto" if r.source == "promoted" else "✎"
        table.add_row(r.name, r.scope, src)
    return Panel(table, title=f"Guardrails ({len(rows)})", border_style="green")


def _footer_panel_v2() -> Panel:
    """Footer with keyboard hints."""
    body = "[dim]Ctrl-C[/] quit   [dim]│[/]   Refreshes every 1s   [dim]│[/]   Read-only control center"
    return Panel(Text.from_markup(body), border_style="dim", padding=(0, 1))


# ---------------------------------------------------------------------------
# Legacy panels (preserved for backwards compatibility with existing tests)
# ---------------------------------------------------------------------------

def _header_panel(watch_dir: str, db_path: pathlib.Path, now: float) -> Panel:
    ts = _dt.datetime.fromtimestamp(now).strftime("%H:%M:%S")
    db_note = "" if db_path.exists() else "  [yellow](no memory.db yet)[/yellow]"
    body = (
        f"[bold cyan]Ollama Sentinel[/] "
        f"[dim]•[/] watching [white]{watch_dir}[/]  "
        f"[dim]•[/] updated [white]{ts}[/]"
        f"{db_note}"
    )
    return Panel(Text.from_markup(body), border_style="cyan")


def _footer_panel() -> Panel:
    return Panel(Text("press Ctrl-C to quit", style="dim"),
                 border_style="dim")


# ---------------------------------------------------------------------------
# Layout assembly
# ---------------------------------------------------------------------------

def render_layout(
    watch_dir: str,
    reviews_dir: pathlib.Path,
    db_path: pathlib.Path,
    reviews: List[ReviewRow],
    violations: List[ViolationRow],
    now: float,
    *,
    config_path: str = "",
    model_name: str = "",
    severity_counts: Optional[Dict[str, int]] = None,
    hottest: Optional[tuple] = None,
    new_this_week: int = 0,
    research_latest: Optional[Dict] = None,
    guardrails: Optional[List[GuardrailRow]] = None,
) -> Layout:
    """Build the full Rich layout for one frame.

    When *config_path* is provided (non-empty), renders the new Control Center
    layout. Otherwise falls back to the legacy two-panel layout for backwards
    compatibility with existing callers and tests.
    """
    if not config_path:
        # Legacy layout (unchanged behavior)
        layout = Layout()
        layout.split_column(
            Layout(name="header", size=3),
            Layout(name="body", ratio=1),
            Layout(name="footer", size=3),
        )
        layout["header"].update(_header_panel(watch_dir, db_path, now))
        layout["body"].split_row(
            Layout(_reviews_panel(reviews, now), name="reviews"),
            Layout(_violations_panel(violations), name="violations"),
        )
        layout["footer"].update(_footer_panel())
        return layout

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
    layout["body"]["right"].split_column(
        Layout(_reviews_rail(reviews, now, -1, 0), name="reviews", ratio=2),
        Layout(_guardrails_panel(guardrails or []), name="guardrails", ratio=1),
    )
    layout["footer"].update(_footer_panel_v2())
    return layout


# ---------------------------------------------------------------------------
# Interactive footer
# ---------------------------------------------------------------------------

def _footer_interactive(ui_state) -> Panel:
    """Mode-aware footer with contextual keyboard hints."""
    from .dashboard_input import Mode

    if ui_state.mode == Mode.FILTER:
        ft = ui_state.filter_text or ""
        body = f"[bold white]/{ft}[/][dim]▏[/]  [dim]Type to filter[/]  [dim]│[/]  Enter apply  [dim]│[/]  Esc cancel"
    elif ui_state.mode == Mode.DETAIL:
        body = "[dim]Esc[/] close  [dim]│[/]  Viewing detail"
    else:
        body = (
            "[dim]q[/] quit  [dim]│[/]  "
            "[dim]Tab[/] focus  [dim]│[/]  "
            "[dim]j/k[/] navigate  [dim]│[/]  "
            "[dim]Enter[/] detail  [dim]│[/]  "
            "[dim]/[/] filter"
        )
    return Panel(Text.from_markup(body), border_style="dim", padding=(0, 1))


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

async def run_dashboard(
    watch_dir: pathlib.Path,
    reviews_dir: pathlib.Path,
    db_path: pathlib.Path,
    *,
    refresh_s: float = 1.0,
    review_limit: int = 15,
    violation_limit: int = 10,
    min_count: int = 2,
    console: Optional[Console] = None,
    shutdown: Optional[asyncio.Event] = None,
    config_path: str = "",
    model_name: str = "",
    interactive: Optional[bool] = None,
) -> None:
    """Render the live dashboard until cancelled or Ctrl-C.

    Reuses one ViolationDB connection across ticks; reopens it on the next
    tick if a query fails (handles DB rotation/replace). Runs filesystem and
    sqlite work in a worker thread to keep the event loop responsive.
    Per-tick exceptions are logged and the affected panel degrades to empty —
    the loop never dies on a transient error.

    Args:
        shutdown: optional Event for graceful external shutdown. When set,
                  the loop exits at the next sleep boundary.
        config_path: path to config file (enables Control Center layout).
        model_name: display name of the configured model.
        interactive: enable keyboard navigation (auto-detects from TTY if None).
    """
    import sys as _sys
    from .dashboard_input import (
        KeyEvent, Mode, PanelId, UIState, apply_key, key_reader_loop,
    )

    console = console or Console()
    shutdown = shutdown or asyncio.Event()
    if interactive is None:
        interactive = _sys.stdin.isatty()

    ui_state = UIState()
    db: Optional[ViolationDB] = None

    def _fetch_data() -> dict:
        nonlocal db
        now = time.time()

        try:
            reviews = recent_reviews(reviews_dir, limit=review_limit if not interactive else 100)
        except Exception:
            log.exception("recent_reviews failed")
            reviews = []

        violations: list = []
        severity_counts: Dict[str, int] = {}
        hottest: Optional[tuple] = None
        new_this_week: int = 0
        guardrails: list = []

        if db is None and db_path.exists():
            try:
                db = ViolationDB(str(db_path))
            except Exception:
                log.exception("ViolationDB open failed: %s", db_path)
                db = None

        if db is not None:
            try:
                violations = top_violations(
                    db, min_count=min_count, limit=violation_limit if not interactive else 50
                )
            except Exception:
                log.exception("top_violations failed; resetting connection")
                with suppress(Exception):
                    db.close()
                db = None

            if db is not None and config_path:
                try:
                    severity_counts = db.count_by_severity()
                except Exception:
                    log.exception("count_by_severity failed")
                try:
                    hot = db.hottest_file(limit=1)
                    hottest = hot[0] if hot else None
                except Exception:
                    log.exception("hottest_file failed")
                try:
                    week_ago = (_dt.datetime.now(_dt.timezone.utc)
                                - _dt.timedelta(days=7)).isoformat()
                    new_this_week = db.count_new_since(week_ago)
                except Exception:
                    log.exception("count_new_since failed")

                try:
                    guardrails = active_guardrails(db)
                except Exception:
                    log.exception("active_guardrails failed")

        research_latest = None
        if config_path:
            try:
                from .research_bridge import load_latest
                research_latest = load_latest(reviews_dir)
            except Exception:
                pass

        return {
            "reviews": reviews,
            "violations": violations,
            "severity_counts": severity_counts,
            "hottest": hottest,
            "new_this_week": new_this_week,
            "research_latest": research_latest,
            "guardrails": guardrails,
            "now": now,
        }

    def _build_layout(data: dict, state: UIState) -> Layout:
        reviews = data["reviews"]
        violations = data["violations"]
        now = data["now"]

        # Apply filter if active
        if state.filter_active and state.filter_text:
            ft = state.filter_text.lower()
            violations = [v for v in violations
                          if ft in v.severity.lower() or ft in v.category.lower()]

        if not config_path:
            return render_layout(
                str(watch_dir), reviews_dir, db_path, reviews, violations, now,
            )

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
        layout["body"]["right"].split_column(
            Layout(reviews_p, name="reviews", ratio=2),
            Layout(_guardrails_panel(data.get("guardrails") or []),
                   name="guardrails", ratio=1),
        )

        if interactive:
            layout["footer"].update(_footer_interactive(state))
        else:
            layout["footer"].update(_footer_panel_v2())

        return layout

    def _effective_counts(data: dict, state: UIState) -> dict:
        """Item counts considering active filter."""
        violations = data["violations"]
        if state.filter_active and state.filter_text:
            ft = state.filter_text.lower()
            violations = [v for v in violations
                          if ft in v.severity.lower() or ft in v.category.lower()]
        return {
            PanelId.REVIEWS: len(data["reviews"]),
            PanelId.PATTERNS: len(violations),
        }

    def _reclamp_selection(state: UIState, data: dict) -> UIState:
        """Re-clamp selection indices after data refresh."""
        counts = _effective_counts(data, state)
        new_sel = dict(state.selection)
        for panel, count in counts.items():
            max_idx = max(0, count - 1) if count > 0 else 0
            if new_sel.get(panel, 0) > max_idx:
                new_sel[panel] = max_idx
        if new_sel != state.selection:
            from .dashboard_input import _copy
            return _copy(state, selection=new_sel)
        return state

    # Key queue for interactive mode
    key_queue: asyncio.Queue = asyncio.Queue()
    reader_task = None

    try:
        data = await asyncio.to_thread(_fetch_data)
        initial_layout = _build_layout(data, ui_state)

        if interactive:
            reader_task = asyncio.create_task(key_reader_loop(key_queue, shutdown))

        with Live(
            initial_layout,
            console=console,
            refresh_per_second=4,
            screen=True,
            transient=True,
        ) as live:
            last_fetch = time.monotonic()

            while not shutdown.is_set():
                if interactive:
                    try:
                        timeout = max(0.05, refresh_s - (time.monotonic() - last_fetch))
                        event = await asyncio.wait_for(key_queue.get(), timeout=timeout)
                        item_counts = _effective_counts(data, ui_state)
                        ui_state = apply_key(ui_state, event, item_counts)
                        if ui_state.quit_requested:
                            break
                        live.update(_build_layout(data, ui_state))
                    except asyncio.TimeoutError:
                        pass
                else:
                    with suppress(asyncio.TimeoutError):
                        await asyncio.wait_for(shutdown.wait(), timeout=refresh_s)
                    if shutdown.is_set():
                        break

                # Refresh data on timer if due
                if time.monotonic() - last_fetch >= refresh_s:
                    data = await asyncio.to_thread(_fetch_data)
                    ui_state = _reclamp_selection(ui_state, data)
                    live.update(_build_layout(data, ui_state))
                    last_fetch = time.monotonic()

    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        if reader_task and not reader_task.done():
            reader_task.cancel()
            with suppress(asyncio.CancelledError):
                await reader_task
        if db is not None:
            with suppress(Exception):
                db.close()


# ---------------------------------------------------------------------------
# Interactive panel variants
# ---------------------------------------------------------------------------

def _patterns_panel_interactive(
    rows: List[ViolationRow], selection: int, scroll: int, title_suffix: str = "",
) -> Panel:
    """Patterns panel with selection highlighting and optional filter indicator."""
    if not rows:
        msg = "no matches" if title_suffix else "no patterns detected yet"
        return Panel(Text(msg, style="dim"),
                     title=f"Patterns{title_suffix}", border_style="magenta")
    visible = rows[scroll:scroll + 15]
    table = Table.grid(padding=(0, 0), expand=True)
    table.add_column(no_wrap=True, overflow="ellipsis")
    for i, r in enumerate(visible):
        abs_idx = scroll + i
        table.add_row(_format_pattern_row(r, selected=(abs_idx == selection)))
    count = len(rows)
    title = f"Patterns ({count}){title_suffix}"
    return Panel(table, title=title, border_style="magenta")


def _detail_panel(state, reviews: List[ReviewRow], violations: List[ViolationRow], now: float) -> Panel:
    """Full-width detail view for the selected item."""
    from .dashboard_input import PanelId

    panel = state.focused_panel
    idx = state.selection.get(panel, 0)

    if panel == PanelId.REVIEWS and idx < len(reviews):
        r = reviews[idx]
        table = Table.grid(padding=(0, 2), expand=True)
        table.add_column(style="dim", no_wrap=True)
        table.add_column()
        table.add_row("File:", r.rel_path)
        table.add_row("Last reviewed:", _format_ago(r.mtime, now))
        table.add_row("Modified:", _dt.datetime.fromtimestamp(r.mtime).strftime("%Y-%m-%d %H:%M:%S"))
        return Panel(table, title=f"Review: {r.rel_path}", border_style="bold blue")

    if panel == PanelId.PATTERNS and idx < len(violations):
        v = violations[idx]
        sev_style = _SEVERITY_STYLE.get(v.severity.lower(), "white")
        table = Table.grid(padding=(0, 2), expand=True)
        table.add_column(style="dim", no_wrap=True)
        table.add_column()
        table.add_row("Severity:", Text(v.severity, style=sev_style))
        table.add_row("Category:", v.category)
        table.add_row("File:", f"{v.file_path}:{v.line_start}-{v.line_end}")
        table.add_row("Occurrences:", f"{v.count}x")
        table.add_row("Description:", v.description)
        return Panel(table, title=f"Pattern: {v.category} in {v.file_path}", border_style="bold magenta")

    return Panel(Text("No item selected", style="dim"), border_style="dim")
