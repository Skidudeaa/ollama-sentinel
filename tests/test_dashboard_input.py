"""Tests for the dashboard input handler and UI state reducer."""
from ollama_sentinel.dashboard_input import (
    Key,
    KeyEvent,
    Mode,
    PanelId,
    UIState,
    apply_key,
)


def _counts(reviews=5, patterns=10):
    return {PanelId.REVIEWS: reviews, PanelId.PATTERNS: patterns}


class TestApplyKeyNormalMode:
    def test_quit_via_char_q(self):
        state = UIState()
        result = apply_key(state, KeyEvent(Key.CHAR, char="q"), _counts())
        assert result.quit_requested is True

    def test_tab_cycles_panels(self):
        state = UIState(focused_panel=PanelId.OVERVIEW)
        state = apply_key(state, KeyEvent(Key.TAB), _counts())
        assert state.focused_panel == PanelId.REVIEWS
        state = apply_key(state, KeyEvent(Key.TAB), _counts())
        assert state.focused_panel == PanelId.PATTERNS
        state = apply_key(state, KeyEvent(Key.TAB), _counts())
        assert state.focused_panel == PanelId.OVERVIEW

    def test_shift_tab_cycles_reverse(self):
        state = UIState(focused_panel=PanelId.OVERVIEW)
        state = apply_key(state, KeyEvent(Key.SHIFT_TAB), _counts())
        assert state.focused_panel == PanelId.PATTERNS

    def test_j_increments_selection(self):
        state = UIState(focused_panel=PanelId.REVIEWS)
        state = apply_key(state, KeyEvent(Key.DOWN), _counts(reviews=5))
        assert state.selection[PanelId.REVIEWS] == 1

    def test_k_decrements_selection(self):
        state = UIState(focused_panel=PanelId.REVIEWS,
                        selection={PanelId.REVIEWS: 3, PanelId.PATTERNS: 0})
        state = apply_key(state, KeyEvent(Key.UP), _counts(reviews=5))
        assert state.selection[PanelId.REVIEWS] == 2

    def test_selection_clamps_at_zero(self):
        state = UIState(focused_panel=PanelId.REVIEWS)
        state = apply_key(state, KeyEvent(Key.UP), _counts(reviews=5))
        assert state.selection[PanelId.REVIEWS] == 0

    def test_selection_clamps_at_max(self):
        state = UIState(focused_panel=PanelId.PATTERNS,
                        selection={PanelId.REVIEWS: 0, PanelId.PATTERNS: 9})
        state = apply_key(state, KeyEvent(Key.DOWN), _counts(patterns=10))
        assert state.selection[PanelId.PATTERNS] == 9

    def test_j_on_overview_is_noop(self):
        state = UIState(focused_panel=PanelId.OVERVIEW)
        result = apply_key(state, KeyEvent(Key.DOWN), _counts())
        assert result.focused_panel == PanelId.OVERVIEW

    def test_slash_enters_filter_mode(self):
        state = UIState(focused_panel=PanelId.REVIEWS)
        state = apply_key(state, KeyEvent(Key.SLASH), _counts())
        assert state.mode == Mode.FILTER
        assert state.focused_panel == PanelId.PATTERNS
        assert state.filter_text == ""

    def test_enter_enters_detail(self):
        state = UIState(focused_panel=PanelId.REVIEWS)
        state = apply_key(state, KeyEvent(Key.ENTER), _counts())
        assert state.mode == Mode.DETAIL

    def test_enter_on_overview_is_noop(self):
        state = UIState(focused_panel=PanelId.OVERVIEW)
        state = apply_key(state, KeyEvent(Key.ENTER), _counts())
        assert state.mode == Mode.NORMAL


class TestApplyKeyFilterMode:
    def test_typing_appends(self):
        state = UIState(mode=Mode.FILTER, filter_text="sec")
        state = apply_key(state, KeyEvent(Key.CHAR, char="u"), _counts())
        assert state.filter_text == "secu"

    def test_backspace_removes(self):
        state = UIState(mode=Mode.FILTER, filter_text="sec")
        state = apply_key(state, KeyEvent(Key.BACKSPACE), _counts())
        assert state.filter_text == "se"

    def test_esc_cancels_filter(self):
        state = UIState(mode=Mode.FILTER, filter_text="sec")
        state = apply_key(state, KeyEvent(Key.ESC), _counts())
        assert state.mode == Mode.NORMAL
        assert state.filter_text == ""
        assert state.filter_active is False

    def test_enter_applies_filter(self):
        state = UIState(mode=Mode.FILTER, filter_text="high")
        state = apply_key(state, KeyEvent(Key.ENTER), _counts())
        assert state.mode == Mode.NORMAL
        assert state.filter_active is True
        assert state.filter_text == "high"

    def test_enter_with_empty_does_not_activate(self):
        state = UIState(mode=Mode.FILTER, filter_text="")
        state = apply_key(state, KeyEvent(Key.ENTER), _counts())
        assert state.filter_active is False

    def test_tab_exits_filter(self):
        state = UIState(mode=Mode.FILTER, focused_panel=PanelId.PATTERNS)
        state = apply_key(state, KeyEvent(Key.TAB), _counts())
        assert state.mode == Mode.NORMAL
        assert state.focused_panel == PanelId.OVERVIEW

    def test_q_types_in_filter_mode(self):
        state = UIState(mode=Mode.FILTER, filter_text="")
        state = apply_key(state, KeyEvent(Key.CHAR, char="q"), _counts())
        assert state.filter_text == "q"
        assert state.quit_requested is False

    def test_j_types_in_filter_mode(self):
        state = UIState(mode=Mode.FILTER, filter_text="")
        state = apply_key(state, KeyEvent(Key.CHAR, char="j"), _counts())
        assert state.filter_text == "j"


class TestApplyKeyDetailMode:
    def test_esc_exits_detail(self):
        state = UIState(mode=Mode.DETAIL)
        state = apply_key(state, KeyEvent(Key.ESC), _counts())
        assert state.mode == Mode.NORMAL

    def test_q_exits_detail_not_quit(self):
        state = UIState(mode=Mode.DETAIL)
        state = apply_key(state, KeyEvent(Key.QUIT), _counts())
        assert state.mode == Mode.NORMAL
        assert state.quit_requested is False

    def test_other_keys_ignored(self):
        state = UIState(mode=Mode.DETAIL)
        result = apply_key(state, KeyEvent(Key.TAB), _counts())
        assert result.mode == Mode.DETAIL


class TestUIStateImmutability:
    def test_apply_key_does_not_mutate_original(self):
        state = UIState(focused_panel=PanelId.OVERVIEW)
        apply_key(state, KeyEvent(Key.TAB), _counts())
        assert state.focused_panel == PanelId.OVERVIEW
