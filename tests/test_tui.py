# tests/test_tui.py
from mid_harness.tui import _rich_available


def test_rich_available():
    assert _rich_available() is True


def test_tui_module_imports():
    from mid_harness.tui import run_tui_session
    assert run_tui_session is not None
