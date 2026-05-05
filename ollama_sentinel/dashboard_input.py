"""Keyboard input handling for the interactive Control Center.

Provides non-blocking key reading via termios/select (macOS/Linux) and a
pure-function state reducer for UI navigation.
"""
from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional


# ---------------------------------------------------------------------------
# Key representation
# ---------------------------------------------------------------------------

class Key(Enum):
    QUIT = auto()
    TAB = auto()
    SHIFT_TAB = auto()
    UP = auto()
    DOWN = auto()
    ENTER = auto()
    ESC = auto()
    SLASH = auto()
    BACKSPACE = auto()
    CHAR = auto()  # carries a character in key_char


@dataclass(frozen=True)
class KeyEvent:
    key: Key
    char: str = ""


# ---------------------------------------------------------------------------
# UI state
# ---------------------------------------------------------------------------

class PanelId(Enum):
    OVERVIEW = 0
    REVIEWS = 1
    PATTERNS = 2


_PANEL_CYCLE = [PanelId.OVERVIEW, PanelId.REVIEWS, PanelId.PATTERNS]


class Mode(Enum):
    NORMAL = auto()
    FILTER = auto()
    DETAIL = auto()


@dataclass
class UIState:
    mode: Mode = Mode.NORMAL
    focused_panel: PanelId = PanelId.OVERVIEW
    selection: dict = field(default_factory=lambda: {
        PanelId.REVIEWS: 0,
        PanelId.PATTERNS: 0,
    })
    scroll_offset: dict = field(default_factory=lambda: {
        PanelId.REVIEWS: 0,
        PanelId.PATTERNS: 0,
    })
    filter_text: str = ""
    filter_active: bool = False
    detail_item: Optional[dict] = None
    quit_requested: bool = False


# ---------------------------------------------------------------------------
# State reducer (pure function)
# ---------------------------------------------------------------------------

def apply_key(state: UIState, event: KeyEvent, item_counts: dict) -> UIState:
    """Apply a key event to the UI state. Returns a new state (non-mutating)."""
    key = event.key

    if state.mode == Mode.DETAIL:
        if key in (Key.ESC, Key.QUIT):
            return _copy(state, mode=Mode.NORMAL, detail_item=None)
        return state

    if state.mode == Mode.FILTER:
        if key == Key.ESC:
            return _copy(state, mode=Mode.NORMAL, filter_text="", filter_active=False)
        if key == Key.ENTER:
            return _copy(state, mode=Mode.NORMAL, filter_active=bool(state.filter_text))
        if key == Key.BACKSPACE:
            return _copy(state, filter_text=state.filter_text[:-1])
        if key == Key.CHAR:
            return _copy(state, filter_text=state.filter_text + event.char)
        if key == Key.TAB:
            return _copy(state, mode=Mode.NORMAL, filter_text="", filter_active=False,
                         focused_panel=_next_panel(state.focused_panel))
        return state

    # Mode.NORMAL
    if key == Key.QUIT:
        return _copy(state, quit_requested=True)

    if key == Key.TAB:
        return _copy(state, focused_panel=_next_panel(state.focused_panel))

    if key == Key.SHIFT_TAB:
        return _copy(state, focused_panel=_prev_panel(state.focused_panel))

    if key == Key.SLASH:
        return _copy(state, mode=Mode.FILTER, focused_panel=PanelId.PATTERNS,
                     filter_text="", filter_active=False)

    if key in (Key.DOWN, Key.UP) and state.focused_panel in (PanelId.REVIEWS, PanelId.PATTERNS):
        panel = state.focused_panel
        max_idx = max(0, item_counts.get(panel, 0) - 1)
        current = state.selection.get(panel, 0)
        if key == Key.DOWN:
            new_idx = min(current + 1, max_idx)
        else:
            new_idx = max(current - 1, 0)
        new_sel = {**state.selection, panel: new_idx}
        new_scroll = _adjust_scroll(state.scroll_offset, panel, new_idx)
        return _copy(state, selection=new_sel, scroll_offset=new_scroll)

    if key == Key.ENTER and state.focused_panel in (PanelId.REVIEWS, PanelId.PATTERNS):
        return _copy(state, mode=Mode.DETAIL)

    return state


def _next_panel(current: PanelId) -> PanelId:
    idx = _PANEL_CYCLE.index(current)
    return _PANEL_CYCLE[(idx + 1) % len(_PANEL_CYCLE)]


def _prev_panel(current: PanelId) -> PanelId:
    idx = _PANEL_CYCLE.index(current)
    return _PANEL_CYCLE[(idx - 1) % len(_PANEL_CYCLE)]


def _adjust_scroll(scroll_offset: dict, panel: PanelId, selection: int, visible: int = 12) -> dict:
    offset = scroll_offset.get(panel, 0)
    if selection < offset:
        offset = selection
    elif selection >= offset + visible:
        offset = selection - visible + 1
    return {**scroll_offset, panel: offset}


def _copy(state: UIState, **overrides) -> UIState:
    return UIState(
        mode=overrides.get("mode", state.mode),
        focused_panel=overrides.get("focused_panel", state.focused_panel),
        selection=overrides.get("selection", state.selection),
        scroll_offset=overrides.get("scroll_offset", state.scroll_offset),
        filter_text=overrides.get("filter_text", state.filter_text),
        filter_active=overrides.get("filter_active", state.filter_active),
        detail_item=overrides.get("detail_item", state.detail_item),
        quit_requested=overrides.get("quit_requested", state.quit_requested),
    )


# ---------------------------------------------------------------------------
# Non-blocking key reader (macOS/Linux only)
# ---------------------------------------------------------------------------

def _read_key_blocking(timeout: float = 0.1) -> Optional[KeyEvent]:
    """Read a single key from stdin in cbreak mode. Returns None on timeout."""
    import select
    import termios
    import tty

    if not sys.stdin.isatty():
        return None

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        rlist, _, _ = select.select([sys.stdin], [], [], timeout)
        if not rlist:
            return None

        ch = sys.stdin.read(1)

        if ch == "\x1b":
            # Possible escape sequence — peek for more
            rlist2, _, _ = select.select([sys.stdin], [], [], 0.05)
            if rlist2:
                ch2 = sys.stdin.read(1)
                if ch2 == "[":
                    ch3 = sys.stdin.read(1)
                    if ch3 == "A":
                        return KeyEvent(Key.UP)
                    if ch3 == "B":
                        return KeyEvent(Key.DOWN)
                    if ch3 == "Z":
                        return KeyEvent(Key.SHIFT_TAB)
                    # Unknown sequence — discard
                    return None
            return KeyEvent(Key.ESC)

        if ch == "\t":
            return KeyEvent(Key.TAB)
        if ch == "\r" or ch == "\n":
            return KeyEvent(Key.ENTER)
        if ch == "\x7f" or ch == "\x08":
            return KeyEvent(Key.BACKSPACE)
        if ch == "q":
            return KeyEvent(Key.QUIT)
        if ch == "/":
            return KeyEvent(Key.SLASH)
        if ch == "j":
            return KeyEvent(Key.DOWN)
        if ch == "k":
            return KeyEvent(Key.UP)
        if ch.isprintable():
            return KeyEvent(Key.CHAR, char=ch)

        return None
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


async def key_reader_loop(
    queue: asyncio.Queue,
    shutdown: asyncio.Event,
    timeout: float = 0.1,
) -> None:
    """Continuously read keys and put them on the queue until shutdown."""
    loop = asyncio.get_event_loop()
    while not shutdown.is_set():
        event = await loop.run_in_executor(None, _read_key_blocking, timeout)
        if event is not None:
            await queue.put(event)
