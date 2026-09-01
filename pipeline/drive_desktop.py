"""Best-effort discovery of Google Drive for desktop's local sync location.

This is informational only — we never write the proxy into this path
ourselves. The proxy is uploaded via the Drive API (see drive_client.py),
and Google Drive for desktop syncs it down to whatever local path it's
configured for on its own, independent of anything this app does. Detection
here exists purely so the CLI can tell you where to point Resolve's Proxy
Media Path, and optionally confirm the sync actually landed.

Two Drive for desktop modes exist, with different detection needs:
  - "Stream" mode (the default): mounts a virtual drive letter. We detect it
    by its Windows volume label ("Google Drive"), since the letter itself is
    user-configurable and not fixed.
  - "Mirror" mode: syncs to an ordinary local folder the user chose at setup
    (no fixed default) — we check a handful of common locations for that
    setup as a fallback guess.

If neither is found, detection returns None rather than guessing wrong, and
callers must treat that as "couldn't confirm the local path" — non-fatal,
since the Drive-side upload already succeeded regardless.
"""

import ctypes
import os
import string
import sys
import time
from pathlib import Path

_MIRROR_MODE_CANDIDATES = [
    "Google Drive/My Drive",
    "My Drive",
    "GoogleDrive/My Drive",
]


def find_local_drive_root() -> Path | None:
    """Returns the local path corresponding to Drive's My Drive root, or None
    if it can't be confirmed. Checks the CLOUDRECORDER_DRIVE_LOCAL_PATH env
    var override first."""
    override = os.environ.get("CLOUDRECORDER_DRIVE_LOCAL_PATH")
    if override:
        path = Path(override)
        return path if path.is_dir() else None

    if sys.platform == "win32":
        stream_mount = _find_stream_mode_mount()
        if stream_mount is not None:
            return stream_mount

    home = Path.home()
    for candidate in _MIRROR_MODE_CANDIDATES:
        path = home / candidate
        if path.is_dir():
            return path

    return None


def _find_stream_mode_mount() -> Path | None:
    try:
        kernel32 = ctypes.windll.kernel32
    except AttributeError:
        return None

    volume_name_buf = ctypes.create_unicode_buffer(1024)
    for letter in string.ascii_uppercase:
        root = f"{letter}:\\"
        try:
            ok = kernel32.GetVolumeInformationW(
                ctypes.c_wchar_p(root), volume_name_buf, ctypes.sizeof(volume_name_buf),
                None, None, None, None, 0,
            )
        except OSError:
            continue
        if ok and volume_name_buf.value.strip().lower() == "google drive":
            candidate = Path(root) / "My Drive"
            if candidate.is_dir():
                return candidate
    return None


def wait_for_local_sync(expected_path: Path, expected_size: int, timeout_seconds: int = 90) -> bool:
    """Polls for expected_path to appear with the expected file size, up to
    timeout_seconds. Returns whether it synced in time — a False here does NOT
    mean anything failed, just that Drive for desktop hasn't caught up yet;
    the file is already safely uploaded regardless."""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if expected_path.is_file() and expected_path.stat().st_size == expected_size:
            return True
        time.sleep(3)
    return False
