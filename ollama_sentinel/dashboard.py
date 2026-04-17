"""Live dashboard for the Ollama Sentinel watcher.

Polls the reviews output directory and the ViolationDB to render a read-only
Rich Live view. Runs as a separate process from ``ollama-sentinel run`` so the
watcher's log output stays untouched.
"""
from __future__ import annotations

import asyncio
import datetime as _dt
import pathlib
import re
from dataclasses import dataclass
from typing import List, Optional

from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .violation_db import ViolationDB


# ---------------------------------------------------------------------------
# Pure data helpers (unit-tested)
# ---------------------------------------------------------------------------

# Reviews saved by processor.save_review() are written as:
#   <stem>.md                          (always-latest)
#   <stem>_YYYYMMDDHHMMSS.md           (versioned snapshots)
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


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

_SEVERITY_STYLE = {
    "critical": "bold red",
    "high": "red",
    "medium": "yellow",
    "low": "dim",
}


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


def render_layout(
    watch_dir: str,
    reviews_dir: pathlib.Path,
    db_path: pathlib.Path,
    reviews: List[ReviewRow],
    violations: List[ViolationRow],
    now: float,
) -> Layout:
    """Build the full Rich layout for one frame."""
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
) -> None:
    """Render the live dashboard until Ctrl-C."""
    console = console or Console()

    def _snapshot() -> Layout:
        import time
        now = time.time()
        reviews = recent_reviews(reviews_dir, limit=review_limit)
        if db_path.exists():
            db = ViolationDB(str(db_path))
            try:
                violations = top_violations(db, min_count=min_count, limit=violation_limit)
            finally:
                db.close()
        else:
            violations = []
        return render_layout(str(watch_dir), reviews_dir, db_path,
                             reviews, violations, now)

    with Live(_snapshot(), console=console, refresh_per_second=4,
              screen=True, transient=True) as live:
        try:
            while True:
                await asyncio.sleep(refresh_s)
                live.update(_snapshot())
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
