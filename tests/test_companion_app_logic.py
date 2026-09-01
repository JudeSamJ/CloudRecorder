"""Tests for companion_app.py's own pure logic (state-aggregation, icon color
selection, log ring buffer) — not Tkinter or pystray themselves.

This sandbox has no Windows tray backend and no matching Tkinter build for the
sandboxed Python, so tkinter/pystray are stubbed just enough to let
companion_app's module-level imports succeed; every test here then exercises
real code in companion_app.py, not the stubs.
"""

import sys
import types

import pytest


def _install_stub(name, **attrs):
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    sys.modules[name] = module
    return module


class _FakeWidget:
    def __init__(self, *a, **k):
        pass

    def __getattr__(self, _name):
        return lambda *a, **k: None


@pytest.fixture(scope="module", autouse=True)
def _stub_gui_modules():
    _install_stub("tkinter", Tk=_FakeWidget, Toplevel=_FakeWidget, Frame=_FakeWidget,
                   Button=_FakeWidget, Label=_FakeWidget, Text=_FakeWidget)
    _install_stub("tkinter.ttk", Treeview=_FakeWidget)
    _install_stub("tkinter.messagebox", showinfo=lambda *a, **k: None,
                   showerror=lambda *a, **k: None, showwarning=lambda *a, **k: None)

    class _FakeMenuItem:
        def __init__(self, *a, **k):
            pass

    class _FakeMenu:
        SEPARATOR = None

        def __init__(self, *a, **k):
            pass

    class _FakeIcon:
        def __init__(self, *a, **k):
            pass

    _install_stub("pystray", MenuItem=_FakeMenuItem, Menu=_FakeMenu, Icon=_FakeIcon)
    yield
    for name in ("tkinter", "tkinter.ttk", "tkinter.messagebox", "pystray"):
        sys.modules.pop(name, None)


@pytest.fixture(autouse=True)
def _companion_app(_stub_gui_modules):
    import companion_app as ca
    return ca


def test_make_icon_image_is_correct_size_and_mode():
    import companion_app as ca

    img = ca._make_icon_image("#3DA65D")
    assert img.size == (64, 64)
    assert img.mode == "RGBA"


def test_aggregate_status_empty_store_is_green(state_store):
    import companion_app as ca

    counts, color = ca._aggregate_status()
    assert counts == {}
    assert color == "#3DA65D"


def test_aggregate_status_any_failed_is_red_even_with_others_ready(state_store):
    import companion_app as ca

    state_store.add_new_session("s1", "P", 1, 1, "m1")
    state_store.set_stage("s1", state_store.READY)
    state_store.add_new_session("s2", "P", 1, 1, "m2")
    state_store.set_stage("s2", state_store.FAILED_PROXY, "boom")

    counts, color = ca._aggregate_status()
    assert color == "#D64545"
    assert counts[state_store.READY] == 1
    assert counts[state_store.FAILED_PROXY] == 1


def test_aggregate_status_working_without_failures_is_amber(state_store):
    import companion_app as ca

    state_store.add_new_session("s1", "P", 1, 1, "m1")
    state_store.set_stage("s1", state_store.GENERATING_PROXY)

    _, color = ca._aggregate_status()
    assert color == "#E8A33D"


def test_log_appends_with_session_id_and_message():
    import companion_app as ca

    ca._recent_log.clear()
    ca._log("session-abc", "something happened")
    snapshot = ca._recent_log_snapshot()
    assert len(snapshot) == 1
    assert "session-abc" in snapshot[0]
    assert "something happened" in snapshot[0]


def test_log_with_no_session_id_uses_placeholder():
    import companion_app as ca

    ca._recent_log.clear()
    ca._log("", "global message")
    assert "-: global message" in ca._recent_log_snapshot()[0]


def test_log_ring_buffer_caps_at_max_lines():
    import companion_app as ca

    ca._recent_log.clear()
    total = ca._MAX_LOG_LINES + 25
    for i in range(total):
        ca._log("s", f"line {i}")
    snapshot = ca._recent_log_snapshot()
    assert len(snapshot) == ca._MAX_LOG_LINES
    assert f"line {total - 1}" in snapshot[-1]
    assert "line 0" not in snapshot[0]


def test_stage_labels_cover_every_state_store_stage(state_store):
    import companion_app as ca

    all_stages = {
        state_store.AWAITING_RECONSTRUCTION, state_store.RECONSTRUCTING,
        state_store.AWAITING_PROXY, state_store.GENERATING_PROXY,
        state_store.READY, state_store.FAILED_RECONSTRUCTION, state_store.FAILED_PROXY,
    }
    assert all_stages.issubset(ca._STAGE_LABELS.keys())
