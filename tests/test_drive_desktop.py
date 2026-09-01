import time
from pathlib import Path

from pipeline import drive_desktop


def test_find_local_drive_root_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("CLOUDRECORDER_DRIVE_LOCAL_PATH", str(tmp_path))
    assert drive_desktop.find_local_drive_root() == tmp_path


def test_find_local_drive_root_env_override_nonexistent_dir_returns_none(monkeypatch):
    monkeypatch.setenv("CLOUDRECORDER_DRIVE_LOCAL_PATH", "/definitely/does/not/exist/xyz")
    assert drive_desktop.find_local_drive_root() is None


def test_find_local_drive_root_no_override_no_mirror_dir_returns_none(tmp_path, monkeypatch):
    monkeypatch.delenv("CLOUDRECORDER_DRIVE_LOCAL_PATH", raising=False)
    monkeypatch.setattr(drive_desktop.sys, "platform", "linux")
    monkeypatch.setattr(drive_desktop.Path, "home", staticmethod(lambda: tmp_path))
    assert drive_desktop.find_local_drive_root() is None


def test_find_local_drive_root_mirror_mode_fallback(tmp_path, monkeypatch):
    monkeypatch.delenv("CLOUDRECORDER_DRIVE_LOCAL_PATH", raising=False)
    monkeypatch.setattr(drive_desktop.sys, "platform", "linux")
    monkeypatch.setattr(drive_desktop.Path, "home", staticmethod(lambda: tmp_path))
    mirror_dir = tmp_path / "Google Drive" / "My Drive"
    mirror_dir.mkdir(parents=True)
    assert drive_desktop.find_local_drive_root() == mirror_dir


def test_wait_for_local_sync_returns_true_when_file_matches(tmp_path):
    target = tmp_path / "file.mov"
    target.write_bytes(b"x" * 100)
    assert drive_desktop.wait_for_local_sync(target, expected_size=100, timeout_seconds=2) is True


def test_wait_for_local_sync_returns_false_on_timeout_when_missing(tmp_path):
    target = tmp_path / "never_appears.mov"
    start = time.monotonic()
    result = drive_desktop.wait_for_local_sync(target, expected_size=100, timeout_seconds=1)
    elapsed = time.monotonic() - start
    assert result is False
    assert elapsed < 5  # sanity: didn't hang way past the timeout


def test_wait_for_local_sync_returns_false_when_size_mismatches(tmp_path):
    target = tmp_path / "file.mov"
    target.write_bytes(b"x" * 50)  # wrong size, e.g. still mid-sync
    assert drive_desktop.wait_for_local_sync(target, expected_size=100, timeout_seconds=1) is False
