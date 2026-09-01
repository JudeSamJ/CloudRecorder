"""Automatic proxy generation from a Phase 4 master video.

Codec/resolution choice: DNxHR LB, half of the master's resolution, PCM audio,
in a .mov container. DNxHR is Avid's openly-specified, cross-platform editing
codec and is still the standard recommendation for Resolve editing proxies on
Windows (unlike ProRes, which historically has been more Mac-native, though
ffmpeg's prores_ks encoder does work fine on Windows too — DNxHR remains the
more conventional choice for a Resolve+Windows workflow and is what we're
sticking with here). LB ("Low Bandwidth") is the profile explicitly meant for
half/quarter-res proxy work, at an intra-frame-only bitrate that keeps
scrubbing responsive in the editor — the entire point of a proxy, unlike a
delivery codec like H.264 which trades scrub performance for compression.

Hardware acceleration honesty check: NVENC/Quick Sync only expose hardware
ENCODERS for H.264/HEVC/AV1 — there is no hardware DNxHR encoder in ffmpeg.
So "hardware acceleration" here means hardware-accelerated *decoding* of the
source master (via -hwaccel cuda/qsv) while the DNxHR encode itself stays on
CPU; if hardware decode isn't available or fails, this falls back to plain
software decode+encode rather than failing the job.

Filename convention for Resolve auto-relink: the proxy keeps the exact same
filename stem as the master (only the extension changes, e.g.
"<sessionId>_master.mp4" -> "<sessionId>_master.mov"). Resolve's Project
Settings -> Master Project Settings -> "Proxy media path" links proxies to
their originals by matching filename stem regardless of extension — once
that setting points at this project's local (Drive-synced) Proxy/ folder,
same-named files there are picked up automatically, without a manual "Link
Proxy Media" pass per clip.
"""

import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from pipeline import ffmpeg_tools
from pipeline.drive_client import DriveClient
from pipeline.drive_desktop import find_local_drive_root, wait_for_local_sync
from pipeline.errors import (
    MasterNotFoundError,
    ProxyAlreadyExistsError,
    ProxyValidationError,
)

DURATION_TOLERANCE_SECONDS = 2.0
DURATION_TOLERANCE_FRACTION = 0.02
PROXY_CODEC_DESCRIPTION = "DNxHR LB, half-resolution, PCM audio, .mov container"


@dataclass
class ProxyResult:
    proxy_name: str
    proxy_file_id: str
    duration_seconds: float
    size_bytes: int
    width: int
    height: int
    hardware_decode_used: str | None
    local_synced_path: Path | None
    local_sync_confirmed: bool


class ProxyGenerator:
    def __init__(self, client: DriveClient | None = None, on_progress: Callable[[str], None] | None = None):
        self._client = client or DriveClient()
        self._on_progress = on_progress or (lambda _msg: None)

    def generate_for_session(self, session_id: str) -> ProxyResult:
        ffmpeg_tools.check_available()

        self._progress(f"Looking up reconstructed master for session '{session_id}'...")
        master = self._find_master(session_id)

        existing_proxy = self._find_existing_proxy(session_id)
        if existing_proxy:
            raise ProxyAlreadyExistsError(
                f"A proxy for session '{session_id}' already exists in Drive "
                f"(id={existing_proxy['id']}). Delete it first if you want to regenerate it."
            )

        original_folder_id = master["parents"][0]
        original_folder = self._client.get_file(original_folder_id, fields="id, name, parents")
        project_folder_id = original_folder["parents"][0]
        project_folder = self._client.get_file(project_folder_id, fields="id, name")
        proxy_folder = self._client.ensure_folder("Proxy", project_folder_id)

        master_stem = Path(master["name"]).stem
        proxy_name = f"{master_stem}.mov"

        temp_dir = Path(tempfile.mkdtemp(prefix=f"cloudrecorder_proxy_{session_id}_"))
        try:
            master_path = temp_dir / master["name"]
            self._progress(f"Downloading master '{master['name']}'...")
            self._client.download_file(master["id"], master_path)

            source_duration = ffmpeg_tools.probe_duration_seconds(master_path)
            source_width, source_height = ffmpeg_tools.probe_resolution(master_path)
            target_width = _even(source_width // 2)
            target_height = _even(source_height // 2)

            hwaccel = ffmpeg_tools.detect_hwaccel()
            self._progress(
                f"Encoding proxy ({PROXY_CODEC_DESCRIPTION}) at {target_width}x{target_height}"
                f"{f', trying {hwaccel}-accelerated decode' if hwaccel else ' (software decode)'}..."
            )
            proxy_path = temp_dir / proxy_name

            def report(percent: float, eta_seconds: float | None) -> None:
                eta_text = f", ETA {eta_seconds:.0f}s" if eta_seconds is not None else ""
                self._progress(f"  encoding: {percent:.0f}%{eta_text}")

            ffmpeg_tools.encode_proxy(
                master_path, proxy_path, target_width, target_height,
                source_duration, hwaccel, on_progress=report,
            )

            self._progress("Validating proxy...")
            proxy_duration = ffmpeg_tools.probe_duration_seconds(proxy_path)
            tolerance = max(DURATION_TOLERANCE_SECONDS, source_duration * DURATION_TOLERANCE_FRACTION)
            diff = abs(proxy_duration - source_duration)
            if diff > tolerance:
                raise ProxyValidationError(
                    f"Proxy duration {proxy_duration:.2f}s differs from master duration "
                    f"{source_duration:.2f}s by {diff:.2f}s, exceeding tolerance {tolerance:.2f}s. "
                    "Proxy was NOT uploaded."
                )

            decode_error = ffmpeg_tools.check_decodes_cleanly(proxy_path)
            if decode_error:
                raise ProxyValidationError(
                    f"Proxy failed the decode integrity check: {decode_error}. Proxy was NOT uploaded."
                )

            proxy_size = proxy_path.stat().st_size

            self._progress(f"Uploading proxy '{proxy_name}' to Drive...")
            proxy_file_id = self._client.upload_file(
                proxy_path, proxy_folder["id"], proxy_name,
                mime_type="video/quicktime",
                progress_callback=lambda frac: self._progress(f"  upload progress: {frac * 100:.0f}%"),
                app_properties={"sessionId": session_id, "kind": "proxy", "masterFileId": master["id"]},
            )

            local_root = find_local_drive_root()
            local_path = None
            synced = False
            if local_root:
                local_path = local_root / "Content Creation" / "Projects" / project_folder["name"] / "Proxy" / proxy_name
                self._progress(f"Waiting for local sync at {local_path}...")
                synced = wait_for_local_sync(local_path, proxy_size, timeout_seconds=90)

            return ProxyResult(
                proxy_name=proxy_name,
                proxy_file_id=proxy_file_id,
                duration_seconds=proxy_duration,
                size_bytes=proxy_size,
                width=target_width,
                height=target_height,
                hardware_decode_used=hwaccel,
                local_synced_path=local_path,
                local_sync_confirmed=synced,
            )
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def _progress(self, message: str) -> None:
        self._on_progress(message)

    def _find_master(self, session_id: str) -> dict:
        fields = "id, name, parents, appProperties"
        candidates = self._client.find_files_by_app_property("sessionId", session_id, fields)
        masters = [f for f in candidates if _kind(f) == "master"]
        if not masters:
            raise MasterNotFoundError(
                f"No reconstructed master found for session '{session_id}'. "
                "Run 'reconstruct' for this session first."
            )
        return masters[0]

    def _find_existing_proxy(self, session_id: str) -> dict | None:
        fields = "id, name, appProperties"
        candidates = self._client.find_files_by_app_property("sessionId", session_id, fields)
        proxies = [f for f in candidates if _kind(f) == "proxy"]
        return proxies[0] if proxies else None


def find_sessions_needing_proxy(client: DriveClient) -> list[str]:
    """Returns session IDs that have a reconstructed master but no proxy yet."""
    fields = "id, appProperties"
    all_tagged = client.find_files_by_app_property("kind", "master", fields)
    master_sessions = {f["appProperties"]["sessionId"] for f in all_tagged if f.get("appProperties", {}).get("sessionId")}

    proxied = client.find_files_by_app_property("kind", "proxy", fields)
    proxy_sessions = {f["appProperties"]["sessionId"] for f in proxied if f.get("appProperties", {}).get("sessionId")}

    return sorted(master_sessions - proxy_sessions)


def _kind(file_entry: dict) -> str | None:
    return (file_entry.get("appProperties") or {}).get("kind")


def _even(value: int) -> int:
    return value if value % 2 == 0 else value - 1
