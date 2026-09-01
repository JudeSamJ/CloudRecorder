import pytest

from pipeline import resolve_bridge
from pipeline.errors import LocalSyncNotFoundError
from pipeline.state_store import SessionRecord


def _session(**overrides):
    defaults = dict(
        session_id="20260901_120000",
        project_name="YouTube_003",
        chunk_count=5,
        total_bytes=1000,
        marker_file_id="marker-1",
        stage="READY",
        paused=False,
        error_message=None,
        master_file_id="master-1",
        proxy_file_id="proxy-1",
        local_proxy_path=None,
        created_at_ms=0,
        updated_at_ms=0,
    )
    defaults.update(overrides)
    return SessionRecord(**defaults)


def test_resolve_project_name_format():
    session = _session(project_name="MyShow", session_id="20260101_090000")
    assert resolve_bridge.resolve_project_name(session) == "MyShow - 20260101_090000"


def test_checklist_all_ok_true_when_every_step_ok():
    checklist = resolve_bridge.ResolveChecklist()
    checklist.add("step 1", True, "")
    checklist.add("step 2", True, "detail")
    assert checklist.all_ok is True


def test_checklist_all_ok_false_when_any_step_fails():
    checklist = resolve_bridge.ResolveChecklist()
    checklist.add("step 1", True, "")
    checklist.add("step 2", False, "went wrong")
    assert checklist.all_ok is False


def test_checklist_render_marks_ok_and_failed_steps():
    checklist = resolve_bridge.ResolveChecklist()
    checklist.add("Connect to Resolve", True, "")
    checklist.add("Link proxy media", False, "manual step needed")
    rendered = checklist.render()
    assert "[x] Connect to Resolve" in rendered
    assert "[ ] Link proxy media — manual step needed" in rendered


def test_checklist_empty_is_all_ok_vacuously_true():
    assert resolve_bridge.ResolveChecklist().all_ok is True


def test_local_paths_for_session_uses_naming_convention(tmp_path, monkeypatch):
    monkeypatch.setattr(resolve_bridge, "find_local_drive_root", lambda: tmp_path)
    session = _session(session_id="SID1", project_name="ProjA")
    original_path, proxy_path = resolve_bridge._local_paths_for_session(session)
    assert original_path == tmp_path / "Content Creation" / "Projects" / "ProjA" / "Original" / "SID1_master.mp4"
    assert proxy_path == tmp_path / "Content Creation" / "Projects" / "ProjA" / "Proxy" / "SID1_master.mov"


def test_local_paths_for_session_raises_when_drive_root_not_found(monkeypatch):
    monkeypatch.setattr(resolve_bridge, "find_local_drive_root", lambda: None)
    with pytest.raises(LocalSyncNotFoundError):
        resolve_bridge._local_paths_for_session(_session())


class _FakeProject:
    """Simulates a Resolve project whose GetSetting/SetSetting behavior is
    controlled per-test, since we can't test against a real Resolve here."""

    def __init__(self, settings: dict):
        self._settings = settings

    def GetSetting(self, key):
        return self._settings.get(key)

    def SetSetting(self, key, value):
        self._settings[key] = value
        return True


def test_set_prefer_proxies_succeeds_when_key_exists_and_takes():
    project = _FakeProject({"perfProxyMediaMode": "0"})
    ok, detail = resolve_bridge.set_prefer_proxies(project)
    assert ok is True
    assert "perfProxyMediaMode" in detail


def test_set_prefer_proxies_fails_gracefully_when_no_candidate_key_exists():
    project = _FakeProject({})  # neither candidate key present
    ok, detail = resolve_bridge.set_prefer_proxies(project)
    assert ok is False
    assert "manually" in detail.lower()


def test_set_prefer_proxies_does_not_raise_on_project_that_throws():
    class ExplodingProject:
        def GetSetting(self, key):
            raise RuntimeError("scripting API not ready")

    ok, detail = resolve_bridge.set_prefer_proxies(ExplodingProject())
    assert ok is False  # must degrade gracefully, never crash open_in_resolve over this
