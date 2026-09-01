#!/usr/bin/env python3
"""Phase 6 desktop companion: a Windows system tray app that watches Drive for
completed recording sessions (Phase 3's completion marker) and automatically
chains Phase 4 reconstruction -> Phase 5 proxy generation, with a dashboard window
for status, failures, and manual overrides.

Why a tray app, not a Windows service: a service runs in Session 0 with no UI
access at all, so it could not show status even in principle — the opposite of
what was asked for ("favor visibility"). A tray app gives an always-visible icon
plus an on-demand full dashboard, at no real cost in a single-user local app.

Threading model:
  - Tkinter's mainloop owns the main thread (hidden root window, shown only when
    the dashboard is opened) — Tk widgets must only ever be touched from this thread.
  - The tray icon runs via pystray's Windows backend `run_detached()`, which pumps
    its own Win32 message loop on a separate thread pystray manages itself.
  - The orchestrator poll loop runs on its own daemon thread.
  - Tray-menu callbacks (which fire on pystray's thread) never touch Tk widgets
    directly — they schedule work onto the Tk thread via root.after(0, ...).

This is a companion layer on top of the existing pipeline/ package, not a
replacement: `python drive_manager.py reconstruct <id>` / `generate-proxy <id>`
still work standalone exactly as before: run either at the same time (e.g. manually
force one session while the tray watches everything else) without conflict, since
both just call the same pipeline.reconstruction/proxy_generation modules against
the same Drive state.
"""

import sys
import threading
import tkinter as tk
from tkinter import messagebox, ttk

import pystray
from PIL import Image, ImageDraw

from pipeline import resolve_bridge
from pipeline import state_store as store
from pipeline.errors import PipelineError
from pipeline.orchestrator import Orchestrator

POLL_INTERVAL_SECONDS = 30

_log_lock = threading.Lock()
_recent_log: list[str] = []
_MAX_LOG_LINES = 200

_STAGE_LABELS = {
    store.AWAITING_RECONSTRUCTION: "Awaiting reconstruction",
    store.RECONSTRUCTING: "Reconstructing...",
    store.AWAITING_PROXY: "Awaiting proxy generation",
    store.GENERATING_PROXY: "Generating proxy...",
    store.READY: "Ready to edit",
    store.FAILED_RECONSTRUCTION: "FAILED (reconstruction)",
    store.FAILED_PROXY: "FAILED (proxy generation)",
}


def _log(session_id: str, message: str) -> None:
    import time

    line = f"[{time.strftime('%H:%M:%S')}] {session_id or '-'}: {message}"
    with _log_lock:
        _recent_log.append(line)
        del _recent_log[:-_MAX_LOG_LINES]
    print(line)


def _recent_log_snapshot() -> list[str]:
    with _log_lock:
        return list(_recent_log)


def _poll_loop(orchestrator: Orchestrator, stop_event: threading.Event) -> None:
    # Run immediately on startup (covers "app was closed, sessions finished
    # uploading in the meantime" — constraint #7) rather than waiting out the
    # first interval.
    while not stop_event.is_set():
        try:
            orchestrator.poll_once()
        except Exception as exc:  # noqa: BLE001 - keep the loop alive no matter what
            _log("", f"Unexpected orchestrator error (will retry next cycle): {exc}")
        stop_event.wait(POLL_INTERVAL_SECONDS)


def _make_icon_image(color: str) -> Image.Image:
    size = 64
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((4, 4, size - 4, size - 4), fill=color)
    return image


def _aggregate_status() -> tuple[dict[str, int], str]:
    sessions = store.list_sessions()
    counts: dict[str, int] = {}
    for session in sessions:
        counts[session.stage] = counts.get(session.stage, 0) + 1
    failed = counts.get(store.FAILED_RECONSTRUCTION, 0) + counts.get(store.FAILED_PROXY, 0)
    active = sum(
        counts.get(stage, 0)
        for stage in (store.RECONSTRUCTING, store.GENERATING_PROXY, store.AWAITING_RECONSTRUCTION, store.AWAITING_PROXY)
    )
    if failed:
        color = "#D64545"  # red — needs your attention
    elif active:
        color = "#E8A33D"  # amber — working
    else:
        color = "#3DA65D"  # green — idle, nothing waiting
    return counts, color


class Dashboard:
    """A single Toplevel window, created once and shown/hidden rather than
    recreated — avoids re-registering a fresh Tk root per open from a background
    thread, which Tkinter does not support safely."""

    def __init__(self, root: tk.Tk, orchestrator: Orchestrator):
        self._root = root
        self._orchestrator = orchestrator
        self._window: tk.Toplevel | None = None
        self._tree: ttk.Treeview | None = None
        self._log_text: tk.Text | None = None

    def show(self) -> None:
        if self._window is not None and self._window.winfo_exists():
            self._window.deiconify()
            self._window.lift()
            return
        self._build()

    def _build(self) -> None:
        window = tk.Toplevel(self._root)
        window.title("CloudRecorder — Companion")
        window.geometry("900x520")
        self._window = window

        columns = ("session", "project", "stage", "updated", "error")
        tree = ttk.Treeview(window, columns=columns, show="headings", height=12)
        for col, label, width in [
            ("session", "Session", 150),
            ("project", "Project", 140),
            ("stage", "Stage", 180),
            ("updated", "Last updated", 140),
            ("error", "Error", 260),
        ]:
            tree.heading(col, text=label)
            tree.column(col, width=width, anchor="w")
        tree.pack(fill="both", expand=True, padx=8, pady=(8, 4))
        self._tree = tree

        button_row = tk.Frame(window)
        button_row.pack(fill="x", padx=8, pady=4)
        tk.Button(button_row, text="Process now", command=self._on_process_now).pack(side="left", padx=2)
        tk.Button(button_row, text="Retry failed", command=self._on_retry).pack(side="left", padx=2)
        tk.Button(button_row, text="Pause", command=lambda: self._set_paused(True)).pack(side="left", padx=2)
        tk.Button(button_row, text="Resume", command=lambda: self._set_paused(False)).pack(side="left", padx=2)
        tk.Button(button_row, text="Open in Resolve", command=self._on_open_resolve).pack(side="left", padx=2)
        tk.Button(button_row, text="Refresh now", command=self._refresh).pack(side="left", padx=2)

        tk.Label(window, text="Recent activity", anchor="w").pack(fill="x", padx=8)
        log_text = tk.Text(window, height=8, state="disabled")
        log_text.pack(fill="both", expand=False, padx=8, pady=(0, 8))
        self._log_text = log_text

        window.protocol("WM_DELETE_WINDOW", window.withdraw)
        self._refresh()

    def _selected_session_id(self) -> str | None:
        if self._tree is None:
            return None
        selection = self._tree.selection()
        if not selection:
            return None
        return self._tree.item(selection[0], "values")[0]

    def _on_process_now(self) -> None:
        session_id = self._selected_session_id()
        if not session_id:
            messagebox.showinfo("Process now", "Select a session first.")
            return
        threading.Thread(target=self._orchestrator.process_session_now, args=(session_id,), daemon=True).start()

    def _on_retry(self) -> None:
        session_id = self._selected_session_id()
        if not session_id:
            messagebox.showinfo("Retry", "Select a failed session first.")
            return
        store.retry_failed(session_id)
        threading.Thread(target=self._orchestrator.process_session_now, args=(session_id,), daemon=True).start()
        self._refresh()

    def _on_open_resolve(self) -> None:
        session_id = self._selected_session_id()
        if not session_id:
            messagebox.showinfo("Open in Resolve", "Select a session first.")
            return
        session = store.get_session(session_id)
        if session is None:
            messagebox.showerror("Open in Resolve", f"Session '{session_id}' not found.")
            return
        if session.stage != store.READY:
            messagebox.showinfo(
                "Open in Resolve",
                f"Session '{session_id}' is at stage '{_STAGE_LABELS.get(session.stage, session.stage)}', "
                "not Ready to edit yet.",
            )
            return

        _log(session_id, "Opening in Resolve...")
        threading.Thread(target=self._run_open_resolve, args=(session,), daemon=True).start()

    def _run_open_resolve(self, session) -> None:
        try:
            checklist = resolve_bridge.open_in_resolve(session)
        except PipelineError as exc:
            _log(session.session_id, f"Open in Resolve failed: {exc}")
            self._window.after(0, lambda: messagebox.showerror("Open in Resolve", str(exc)))
            return

        for step, ok, detail in checklist.steps:
            _log(session.session_id, f"{'OK' if ok else 'MISSING'}: {step}{' — ' + detail if detail else ''}")

        title = "Open in Resolve — done" if checklist.all_ok else "Open in Resolve — needs your attention"
        show = messagebox.showinfo if checklist.all_ok else messagebox.showwarning
        self._window.after(0, lambda: show(title, checklist.render()))

    def _set_paused(self, paused: bool) -> None:
        session_id = self._selected_session_id()
        if not session_id:
            messagebox.showinfo("Pause/Resume", "Select a session first.")
            return
        store.set_paused(session_id, paused)
        self._refresh()

    def _refresh(self) -> None:
        if self._window is None or not self._window.winfo_exists():
            return
        if self._tree is not None:
            self._tree.delete(*self._tree.get_children())
            for session in store.list_sessions():
                import time as _time

                updated = _time.strftime("%Y-%m-%d %H:%M", _time.localtime(session.updated_at_ms / 1000))
                stage_label = _STAGE_LABELS.get(session.stage, session.stage)
                if session.paused:
                    stage_label += " (paused)"
                self._tree.insert(
                    "", "end",
                    values=(session.session_id, session.project_name, stage_label, updated, session.error_message or ""),
                )
        if self._log_text is not None:
            self._log_text.configure(state="normal")
            self._log_text.delete("1.0", "end")
            self._log_text.insert("end", "\n".join(_recent_log_snapshot()[-100:]))
            self._log_text.see("end")
            self._log_text.configure(state="disabled")
        self._window.after(2000, self._refresh)


def _build_tray_icon(root: tk.Tk, dashboard: Dashboard, stop_event: threading.Event) -> pystray.Icon:
    def open_dashboard(_icon=None, _item=None) -> None:
        root.after(0, dashboard.show)

    def status_text(_item) -> str:
        counts, _ = _aggregate_status()
        ready = counts.get(store.READY, 0)
        failed = counts.get(store.FAILED_RECONSTRUCTION, 0) + counts.get(store.FAILED_PROXY, 0)
        working = sum(
            counts.get(s, 0) for s in (store.RECONSTRUCTING, store.GENERATING_PROXY)
        )
        waiting = counts.get(store.AWAITING_RECONSTRUCTION, 0) + counts.get(store.AWAITING_PROXY, 0)
        return f"{ready} ready · {working} working · {waiting} waiting · {failed} failed"

    def quit_app(icon: pystray.Icon, _item=None) -> None:
        stop_event.set()
        icon.stop()
        root.after(0, root.quit)

    menu = pystray.Menu(
        pystray.MenuItem(status_text, None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Open Dashboard", open_dashboard, default=True),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit", quit_app),
    )
    _, color = _aggregate_status()
    icon = pystray.Icon("cloudrecorder-companion", _make_icon_image(color), "CloudRecorder Companion", menu)
    return icon


def _icon_refresh_loop(icon: pystray.Icon, stop_event: threading.Event) -> None:
    while not stop_event.is_set():
        _, color = _aggregate_status()
        icon.icon = _make_icon_image(color)
        icon.update_menu()
        stop_event.wait(5)


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

    store.init_db()

    root = tk.Tk()
    root.withdraw()  # tray-only by default; the dashboard is a Toplevel shown on demand

    orchestrator = Orchestrator(on_progress=_log)
    stop_event = threading.Event()

    threading.Thread(target=_poll_loop, args=(orchestrator, stop_event), daemon=True).start()

    dashboard = Dashboard(root, orchestrator)
    icon = _build_tray_icon(root, dashboard, stop_event)
    threading.Thread(target=_icon_refresh_loop, args=(icon, stop_event), daemon=True).start()

    icon.run_detached()
    root.mainloop()


if __name__ == "__main__":
    main()
