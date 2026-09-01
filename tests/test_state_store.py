import time

from pipeline import state_store as store


def test_add_new_session_creates_awaiting_reconstruction_row(state_store):
    state_store.add_new_session("s1", "MyProject", 10, 12345, "marker-id-1")
    session = state_store.get_session("s1")
    assert session is not None
    assert session.project_name == "MyProject"
    assert session.chunk_count == 10
    assert session.total_bytes == 12345
    assert session.marker_file_id == "marker-id-1"
    assert session.stage == store.AWAITING_RECONSTRUCTION
    assert session.paused is False
    assert session.error_message is None


def test_add_new_session_is_idempotent(state_store):
    state_store.add_new_session("s1", "MyProject", 10, 12345, "marker-id-1")
    state_store.set_stage("s1", store.READY)
    # Re-discovering the same marker (e.g. a second poll cycle) must not reset
    # an already-advanced session back to AWAITING_RECONSTRUCTION.
    state_store.add_new_session("s1", "MyProject", 10, 12345, "marker-id-1")
    session = state_store.get_session("s1")
    assert session.stage == store.READY


def test_known_session_ids(state_store):
    assert state_store.known_session_ids() == set()
    state_store.add_new_session("s1", "P", 1, 1, "m1")
    state_store.add_new_session("s2", "P", 1, 1, "m2")
    assert state_store.known_session_ids() == {"s1", "s2"}


def test_list_by_stage_excludes_paused_by_default(state_store):
    state_store.add_new_session("s1", "P", 1, 1, "m1")
    state_store.add_new_session("s2", "P", 1, 1, "m2")
    state_store.set_paused("s1", True)

    unpaused = state_store.list_by_stage(store.AWAITING_RECONSTRUCTION)
    assert {s.session_id for s in unpaused} == {"s2"}

    all_including_paused = state_store.list_by_stage(store.AWAITING_RECONSTRUCTION, include_paused=True)
    assert {s.session_id for s in all_including_paused} == {"s1", "s2"}


def test_set_stage_with_error_message(state_store):
    state_store.add_new_session("s1", "P", 1, 1, "m1")
    state_store.set_stage("s1", store.FAILED_RECONSTRUCTION, "missing chunk 3")
    session = state_store.get_session("s1")
    assert session.stage == store.FAILED_RECONSTRUCTION
    assert session.error_message == "missing chunk 3"


def test_set_master_result_advances_to_awaiting_proxy_and_clears_error(state_store):
    state_store.add_new_session("s1", "P", 1, 1, "m1")
    state_store.set_stage("s1", store.FAILED_RECONSTRUCTION, "transient error")
    state_store.set_master_result("s1", "drive-master-id")
    session = state_store.get_session("s1")
    assert session.stage == store.AWAITING_PROXY
    assert session.master_file_id == "drive-master-id"
    assert session.error_message is None


def test_set_proxy_result_advances_to_ready(state_store):
    state_store.add_new_session("s1", "P", 1, 1, "m1")
    state_store.set_proxy_result("s1", "drive-proxy-id", "/local/path/x.mov")
    session = state_store.get_session("s1")
    assert session.stage == store.READY
    assert session.proxy_file_id == "drive-proxy-id"
    assert session.local_proxy_path == "/local/path/x.mov"


def test_retry_failed_reconstruction_resets_to_awaiting_reconstruction(state_store):
    state_store.add_new_session("s1", "P", 1, 1, "m1")
    state_store.set_stage("s1", store.FAILED_RECONSTRUCTION, "boom")
    state_store.retry_failed("s1")
    session = state_store.get_session("s1")
    assert session.stage == store.AWAITING_RECONSTRUCTION


def test_retry_failed_proxy_resets_to_awaiting_proxy(state_store):
    state_store.add_new_session("s1", "P", 1, 1, "m1")
    state_store.set_master_result("s1", "master-id")
    state_store.set_stage("s1", store.FAILED_PROXY, "boom")
    state_store.retry_failed("s1")
    session = state_store.get_session("s1")
    assert session.stage == store.AWAITING_PROXY


def test_retry_failed_is_noop_for_non_failed_stage(state_store):
    state_store.add_new_session("s1", "P", 1, 1, "m1")
    state_store.retry_failed("s1")  # stage is AWAITING_RECONSTRUCTION, not FAILED_*
    session = state_store.get_session("s1")
    assert session.stage == store.AWAITING_RECONSTRUCTION


def test_retry_failed_unknown_session_does_not_raise(state_store):
    state_store.retry_failed("does-not-exist")  # must not raise


def test_list_sessions_ordered_newest_first(state_store):
    state_store.add_new_session("s1", "P", 1, 1, "m1")
    time.sleep(0.002)  # guarantee a distinct created_at_ms from s1
    state_store.add_new_session("s2", "P", 1, 1, "m2")
    sessions = state_store.list_sessions()
    assert [s.session_id for s in sessions] == ["s2", "s1"]


def test_get_session_missing_returns_none(state_store):
    assert state_store.get_session("nope") is None
