"""Orchestrator tests using fakes for DriveClient/SessionReconstructor/ProxyGenerator
— these are the pieces that would otherwise need a live Google account and real
ffmpeg. Fakes are substituted at the pipeline.orchestrator import site, matching
how the module actually resolves those names."""

from dataclasses import dataclass

import pytest

from pipeline import orchestrator as orch
from pipeline.errors import PipelineError


class FakeDriveClient:
    def __init__(self, markers):
        self._markers = markers

    def find_files_by_app_property(self, key, value, fields):
        assert key == "kind"
        assert value == "session_complete"
        return self._markers


@dataclass
class FakeMasterResult:
    master_name: str
    master_file_id: str


@dataclass
class FakeProxyResult:
    proxy_name: str
    proxy_file_id: str
    local_synced_path: object = None


class FakeReconstructorOK:
    def __init__(self, client=None, on_progress=None):
        self.on_progress = on_progress or (lambda _msg: None)

    def reconstruct(self, session_id):
        self.on_progress(f"reconstructed {session_id}")
        return FakeMasterResult(master_name=f"{session_id}_master.mp4", master_file_id="master-1")


class FakeReconstructorFails:
    def __init__(self, client=None, on_progress=None):
        pass

    def reconstruct(self, session_id):
        raise PipelineError("missing chunk 3")


class FakeProxyGeneratorOK:
    def __init__(self, client=None, on_progress=None):
        self.on_progress = on_progress or (lambda _msg: None)

    def generate_for_session(self, session_id):
        self.on_progress(f"proxy generated for {session_id}")
        return FakeProxyResult(proxy_name=f"{session_id}_master.mov", proxy_file_id="proxy-1")


class FakeProxyGeneratorFails:
    def __init__(self, client=None, on_progress=None):
        pass

    def generate_for_session(self, session_id):
        raise PipelineError("ffmpeg encode failed")


def _marker(session_id, project_name="MyProject", chunk_count=3, total_bytes=999, file_id="marker-file-id"):
    return {
        "id": file_id,
        "appProperties": {
            "sessionId": session_id,
            "kind": "session_complete",
            "projectName": project_name,
            "chunkCount": str(chunk_count),
            "totalBytes": str(total_bytes),
        },
    }


@pytest.fixture
def orchestrator(state_store):
    return orch.Orchestrator(client=FakeDriveClient([]), on_progress=lambda sid, msg: None)


def test_discover_new_sessions_adds_row(state_store):
    o = orch.Orchestrator(client=FakeDriveClient([_marker("s1")]), on_progress=lambda sid, msg: None)
    o._discover_new_sessions()
    session = state_store.get_session("s1")
    assert session is not None
    assert session.stage == state_store.AWAITING_RECONSTRUCTION
    assert session.project_name == "MyProject"
    assert session.chunk_count == 3
    assert session.total_bytes == 999


def test_discover_new_sessions_skips_already_known(state_store):
    state_store.add_new_session("s1", "MyProject", 3, 999, "marker-file-id")
    state_store.set_stage("s1", state_store.READY)
    o = orch.Orchestrator(client=FakeDriveClient([_marker("s1")]), on_progress=lambda sid, msg: None)
    o._discover_new_sessions()
    # Must not have been reset back to AWAITING_RECONSTRUCTION by rediscovery.
    assert state_store.get_session("s1").stage == state_store.READY


def test_discover_new_sessions_handles_marker_missing_session_id(state_store):
    bad_marker = {"id": "x", "appProperties": {"kind": "session_complete"}}  # no sessionId
    o = orch.Orchestrator(client=FakeDriveClient([bad_marker]), on_progress=lambda sid, msg: None)
    o._discover_new_sessions()  # must not raise
    assert state_store.known_session_ids() == set()


def test_reconstruct_success_chains_into_proxy(monkeypatch, state_store):
    monkeypatch.setattr(orch, "SessionReconstructor", FakeReconstructorOK)
    monkeypatch.setattr(orch, "ProxyGenerator", FakeProxyGeneratorOK)
    state_store.add_new_session("s1", "P", 1, 1, "m1")

    events = []
    o = orch.Orchestrator(client=FakeDriveClient([]), on_progress=lambda sid, msg: events.append((sid, msg)))
    o._reconstruct("s1")

    session = state_store.get_session("s1")
    assert session.stage == state_store.READY
    assert session.master_file_id == "master-1"
    assert session.proxy_file_id == "proxy-1"
    assert any("reconstructed s1" in msg for _, msg in events)
    assert any("proxy generated for s1" in msg for _, msg in events)


def test_reconstruct_failure_sets_failed_stage_with_error(monkeypatch, state_store):
    monkeypatch.setattr(orch, "SessionReconstructor", FakeReconstructorFails)
    monkeypatch.setattr(orch, "ProxyGenerator", FakeProxyGeneratorOK)
    state_store.add_new_session("s1", "P", 1, 1, "m1")

    o = orch.Orchestrator(client=FakeDriveClient([]), on_progress=lambda sid, msg: None)
    o._reconstruct("s1")

    session = state_store.get_session("s1")
    assert session.stage == state_store.FAILED_RECONSTRUCTION
    assert "missing chunk 3" in session.error_message
    # Proxy generation must not have run at all after a reconstruction failure.
    assert session.proxy_file_id is None


def test_generate_proxy_failure_sets_failed_proxy_stage(monkeypatch, state_store):
    monkeypatch.setattr(orch, "ProxyGenerator", FakeProxyGeneratorFails)
    state_store.add_new_session("s1", "P", 1, 1, "m1")
    state_store.set_master_result("s1", "master-1")

    o = orch.Orchestrator(client=FakeDriveClient([]), on_progress=lambda sid, msg: None)
    o._generate_proxy("s1")

    session = state_store.get_session("s1")
    assert session.stage == state_store.FAILED_PROXY
    assert "ffmpeg encode failed" in session.error_message


def test_generate_proxy_skips_paused_session(monkeypatch, state_store):
    monkeypatch.setattr(orch, "ProxyGenerator", FakeProxyGeneratorOK)
    state_store.add_new_session("s1", "P", 1, 1, "m1")
    state_store.set_master_result("s1", "master-1")
    state_store.set_paused("s1", True)

    o = orch.Orchestrator(client=FakeDriveClient([]), on_progress=lambda sid, msg: None)
    o._generate_proxy("s1")

    # Must not have advanced to READY while paused.
    assert state_store.get_session("s1").stage == state_store.AWAITING_PROXY


def test_poll_once_full_pipeline(monkeypatch, state_store):
    monkeypatch.setattr(orch, "SessionReconstructor", FakeReconstructorOK)
    monkeypatch.setattr(orch, "ProxyGenerator", FakeProxyGeneratorOK)

    o = orch.Orchestrator(client=FakeDriveClient([_marker("s1")]), on_progress=lambda sid, msg: None)
    o.poll_once()

    session = state_store.get_session("s1")
    assert session.stage == state_store.READY


def test_process_session_now_ignores_paused(monkeypatch, state_store):
    monkeypatch.setattr(orch, "SessionReconstructor", FakeReconstructorOK)
    state_store.add_new_session("s1", "P", 1, 1, "m1")
    state_store.set_paused("s1", True)

    o = orch.Orchestrator(client=FakeDriveClient([]), on_progress=lambda sid, msg: None)
    o.process_session_now("s1")

    assert state_store.get_session("s1").stage == state_store.AWAITING_RECONSTRUCTION


def test_process_session_now_ignores_wrong_stage(monkeypatch, state_store):
    reconstruct_calls = []
    monkeypatch.setattr(orch, "SessionReconstructor", FakeReconstructorOK)
    state_store.add_new_session("s1", "P", 1, 1, "m1")
    state_store.set_stage("s1", state_store.READY)

    o = orch.Orchestrator(client=FakeDriveClient([]), on_progress=lambda sid, msg: None)
    o.process_session_now("s1")  # READY isn't AWAITING_* — must be a no-op

    assert state_store.get_session("s1").stage == state_store.READY


def test_discover_new_sessions_survives_drive_error(state_store):
    class ExplodingClient:
        def find_files_by_app_property(self, *a, **k):
            raise PipelineError("Drive is down")

    events = []
    o = orch.Orchestrator(client=ExplodingClient(), on_progress=lambda sid, msg: events.append(msg))
    o._discover_new_sessions()  # must not raise
    assert any("Drive is down" in msg for msg in events)
