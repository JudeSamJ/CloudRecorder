"""Phase 6 orchestration: chains Phase 4 reconstruction -> Phase 5 proxy generation
automatically, driven entirely by local SQLite state (pipeline.state_store) plus
Drive session-completion markers written by the Phase 3 phone app.

Runs on a single background thread via a plain poll loop (Orchestrator.poll_once,
called repeatedly by companion_app.py). One thread, sessions processed one at a
time — deliberately not concurrent: this is a single-user local app with low
session volume, and running two ffmpeg reconstructions/encodes at once would just
contend for the same CPU/disk with no real benefit, so simplicity wins over
throughput here.

Restart safety (constraint #7): there is no separate "resume" code path. Every
session's current stage lives in the SQLite store, not in memory, so a poll loop
started after the app was closed and reopened just continues from whatever stage
each session's row already says — "picked up on next launch" falls out of the
design rather than needing special-casing.
"""

from dataclasses import dataclass
from typing import Callable

from pipeline import state_store as store
from pipeline.drive_client import DriveClient
from pipeline.errors import PipelineError
from pipeline.proxy_generation import ProxyGenerator
from pipeline.reconstruction import SessionReconstructor

ProgressCallback = Callable[[str, str], None]  # (session_id, message)


@dataclass
class OrchestratorEvent:
    session_id: str
    message: str


class Orchestrator:
    def __init__(self, client: DriveClient | None = None, on_progress: ProgressCallback | None = None):
        self._client = client or DriveClient()
        self._on_progress = on_progress or (lambda _sid, _msg: None)
        store.init_db()

    def poll_once(self) -> None:
        """One full pass: discover newly-completed sessions, then advance every
        eligible session one pipeline step. Safe to call repeatedly and safe to
        interrupt between calls — each call only touches sessions currently due
        for work, and every state change is committed to SQLite immediately."""
        self._discover_new_sessions()
        self._process_awaiting_reconstruction()
        self._process_awaiting_proxy()

    def process_session_now(self, session_id: str) -> None:
        """Manual override: process one specific session immediately regardless of
        the poll interval, if it's currently in an AWAITING_* stage. This does not
        bypass the completeness check — a session only reaches AWAITING_RECONSTRUCTION
        once its Drive completion marker already confirmed every chunk uploaded."""
        session = store.get_session(session_id)
        if session is None or session.paused:
            return
        if session.stage == store.AWAITING_RECONSTRUCTION:
            self._reconstruct(session_id)
        elif session.stage == store.AWAITING_PROXY:
            self._generate_proxy(session_id)

    def _discover_new_sessions(self) -> None:
        try:
            markers = self._client.find_files_by_app_property(
                "kind", "session_complete", "id, appProperties",
            )
        except PipelineError as exc:
            self._on_progress("", f"Could not check Drive for completed sessions: {exc}")
            return

        known = store.known_session_ids()
        for marker in markers:
            props = marker.get("appProperties") or {}
            session_id = props.get("sessionId")
            if not session_id or session_id in known:
                continue
            project_name = props.get("projectName", "Unknown")
            chunk_count = int(props.get("chunkCount", 0) or 0)
            total_bytes = int(props.get("totalBytes", 0) or 0)
            store.add_new_session(session_id, project_name, chunk_count, total_bytes, marker["id"])
            self._on_progress(session_id, f"New completed session found: '{project_name}' ({chunk_count} chunks)")

    def _process_awaiting_reconstruction(self) -> None:
        for session in store.list_by_stage(store.AWAITING_RECONSTRUCTION):
            self._reconstruct(session.session_id)

    def _process_awaiting_proxy(self) -> None:
        for session in store.list_by_stage(store.AWAITING_PROXY):
            self._generate_proxy(session.session_id)

    def _reconstruct(self, session_id: str) -> None:
        store.set_stage(session_id, store.RECONSTRUCTING)
        self._on_progress(session_id, "Reconstruction started...")
        reconstructor = SessionReconstructor(
            client=self._client,
            on_progress=lambda msg: self._on_progress(session_id, msg),
        )
        try:
            result = reconstructor.reconstruct(session_id)
        except PipelineError as exc:
            store.set_stage(session_id, store.FAILED_RECONSTRUCTION, str(exc))
            self._on_progress(session_id, f"Reconstruction FAILED: {exc}")
            return
        store.set_master_result(session_id, result.master_file_id)
        self._on_progress(session_id, f"Reconstruction succeeded: {result.master_name}")
        # Chain straight into proxy generation rather than waiting for the next
        # poll cycle — reconstruction already just proved Drive/network/ffmpeg are
        # working, so there's nothing gained by deferring the next step.
        self._generate_proxy(session_id)

    def _generate_proxy(self, session_id: str) -> None:
        session = store.get_session(session_id)
        if session is None or session.paused:
            return
        store.set_stage(session_id, store.GENERATING_PROXY)
        self._on_progress(session_id, "Proxy generation started...")
        generator = ProxyGenerator(
            client=self._client,
            on_progress=lambda msg: self._on_progress(session_id, msg),
        )
        try:
            result = generator.generate_for_session(session_id)
        except PipelineError as exc:
            store.set_stage(session_id, store.FAILED_PROXY, str(exc))
            self._on_progress(session_id, f"Proxy generation FAILED: {exc}")
            return
        local_path = str(result.local_synced_path) if result.local_synced_path else None
        store.set_proxy_result(session_id, result.proxy_file_id, local_path)
        self._on_progress(session_id, f"Ready to edit: {result.proxy_name}")
