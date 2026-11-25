"""Smoke test: the window can be built and fills itself with data."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

tk = pytest.importorskip("tkinter")


def test_window_builds_and_updates():
    import app
    try:
        window = app.MonitorApp()
    except tk.TclError as exc:
        pytest.skip(f"no display available: {exc}")
    try:
        window.withdraw()
        import monitor
        snap = monitor.full_snapshot()
        window._apply(snap)
        window.update()
        assert len(window.proc_tree.get_children()) == len(snap["processes"])
        assert window.cpu_lbl.cget("text").endswith("%")
        window._sort_by("name")
        window.filter_var.set("python")
        assert len(window.proc_tree.get_children()) >= 1
    finally:
        window.destroy()
