"""Thin wrappers around the ffmpeg/ffprobe CLIs used for chunk reassembly.

Concat method: the concat DEMUXER with stream copy (`-f concat -safe 0 -i
list.txt -c copy`), not the concat protocol (only works for a few formats like
MPEG-TS, not MP4) and not the concat filter (decodes/re-encodes everything,
which is unnecessary and riskier for sync since all chunks in a session share
identical codec settings — they came from the same CameraX Recorder session,
just restarted per segment).
"""

import json
import re
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Callable

from pipeline.errors import FFmpegError, FFmpegNotFoundError

_HWACCEL_CANDIDATES = ["cuda", "qsv"]


def check_available() -> None:
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        raise FFmpegNotFoundError(
            "ffmpeg/ffprobe not found on PATH. Install FFmpeg (https://ffmpeg.org/download.html) "
            "and ensure both ffmpeg and ffprobe are on your system PATH, then try again."
        )


def probe_duration_seconds(path: Path) -> float:
    """Returns the media duration in seconds via ffprobe, or raises FFmpegError if
    the file can't be probed (e.g. it's corrupt)."""
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "json", str(path),
        ],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise FFmpegError(f"ffprobe could not read {path.name}: {result.stderr.strip()}")
    try:
        data = json.loads(result.stdout)
        return float(data["format"]["duration"])
    except (KeyError, ValueError, json.JSONDecodeError) as exc:
        raise FFmpegError(f"ffprobe returned unexpected output for {path.name}: {exc}") from exc


def probe_resolution(path: Path) -> tuple[int, int]:
    """Returns (width, height) of the first video stream via ffprobe."""
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height", "-of", "json", str(path),
        ],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise FFmpegError(f"ffprobe could not read resolution of {path.name}: {result.stderr.strip()}")
    try:
        data = json.loads(result.stdout)
        stream = data["streams"][0]
        return int(stream["width"]), int(stream["height"])
    except (KeyError, IndexError, ValueError, json.JSONDecodeError) as exc:
        raise FFmpegError(f"ffprobe returned unexpected output for {path.name}: {exc}") from exc


def write_concat_list(chunk_paths: list[Path], list_file_path: Path) -> None:
    """Writes an ffmpeg concat-demuxer list file. Paths are our own controlled
    local filenames (chunk_0001.mp4, ...), so no character-escaping is needed."""
    lines = [f"file '{path.as_posix()}'" for path in chunk_paths]
    list_file_path.write_text("\n".join(lines), encoding="utf-8")


def concat_to_master(list_file_path: Path, output_path: Path) -> None:
    """Stream-copies chunks per the concat list into a single master file.
    Raises FFmpegError with ffmpeg's stderr on failure."""
    result = subprocess.run(
        [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", str(list_file_path), "-c", "copy", str(output_path),
        ],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise FFmpegError(f"ffmpeg concat failed: {result.stderr.strip()}")


def check_decodes_cleanly(path: Path) -> str | None:
    """Fully decodes the file without writing output, discarding frames — a fast
    integrity pass that surfaces corruption ffprobe's metadata-only read would
    miss. Returns None if clean, or the captured stderr if ffmpeg reported
    decode errors."""
    result = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-f", "null", "-"],
        capture_output=True, text=True,
    )
    stderr = result.stderr.strip()
    if result.returncode != 0 or stderr:
        return stderr or f"ffmpeg exited with code {result.returncode} during decode check"
    return None


def detect_hwaccel() -> str | None:
    """Returns the first hardware decode acceleration method ffmpeg was built
    with support for (cuda for NVIDIA, qsv for Intel Quick Sync), or None.

    This only confirms ffmpeg *supports* the method, not that a matching GPU is
    actually present and working — callers must still fall back to plain
    software processing if using it fails at encode time. There's no hardware
    DNxHR *encoder* in ffmpeg (NVENC/QSV only expose H.264/HEVC/AV1 encoders),
    so hardware acceleration here applies to decoding the source master only;
    the DNxHR encode itself is always software.
    """
    result = subprocess.run(["ffmpeg", "-hide_banner", "-hwaccels"], capture_output=True, text=True)
    if result.returncode != 0:
        return None
    available = {line.strip().lower() for line in result.stdout.splitlines()}
    for candidate in _HWACCEL_CANDIDATES:
        if candidate in available:
            return candidate
    return None


def encode_proxy(
    input_path: Path,
    output_path: Path,
    width: int,
    height: int,
    total_duration_seconds: float,
    hwaccel: str | None,
    on_progress: Callable[[float, float | None], None] | None = None,
) -> None:
    """Encodes a DNxHR LB proxy (half-res target passed in by the caller) with
    PCM audio in a .mov container, reporting (percent, eta_seconds) via
    on_progress as ffmpeg emits progress. Tries [hwaccel]-accelerated decode
    first if given; on failure, retries once with plain software decode rather
    than failing the whole job over an unavailable/misbehaving GPU path."""

    def run(use_hwaccel: str | None) -> subprocess.CompletedProcess:
        args = ["ffmpeg", "-y"]
        if use_hwaccel:
            args += ["-hwaccel", use_hwaccel]
        args += [
            "-i", str(input_path),
            "-vf", f"scale={width}:{height}",
            "-c:v", "dnxhd", "-profile:v", "dnxhr_lb", "-pix_fmt", "yuv422p",
            "-c:a", "pcm_s16le",
            "-progress", "pipe:1", "-nostats",
            str(output_path),
        ]
        process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

        # ffmpeg's normal logging goes to stderr while -progress writes to
        # stdout; stderr must be drained concurrently on its own thread or a
        # long encode's stderr output can fill the OS pipe buffer and deadlock
        # ffmpeg against us only reading stdout.
        stderr_lines: list[str] = []
        stderr_thread = threading.Thread(target=lambda: stderr_lines.extend(process.stderr), daemon=True)
        stderr_thread.start()

        progress_block: dict[str, str] = {}
        for line in process.stdout:
            line = line.strip()
            if not line or "=" not in line:
                continue
            key, _, value = line.partition("=")
            progress_block[key] = value
            if key == "progress":
                if on_progress:
                    _report_progress(progress_block, total_duration_seconds, on_progress)
                progress_block = {}

        process.wait()
        stderr_thread.join()
        return subprocess.CompletedProcess(args, process.returncode, "", "".join(stderr_lines))

    result = run(hwaccel) if hwaccel else run(None)
    if result.returncode != 0 and hwaccel:
        result = run(None)
    if result.returncode != 0:
        raise FFmpegError(f"ffmpeg proxy encode failed: {result.stderr.strip()}")


def _report_progress(
    block: dict[str, str],
    total_duration_seconds: float,
    on_progress: Callable[[float, float | None], None],
) -> None:
    out_time_us = block.get("out_time_us") or block.get("out_time_ms")
    if out_time_us is None:
        return
    try:
        elapsed_seconds = float(out_time_us) / 1_000_000
    except ValueError:
        return
    percent = min(100.0, (elapsed_seconds / total_duration_seconds) * 100) if total_duration_seconds > 0 else 0.0

    eta_seconds = None
    speed_str = block.get("speed", "").rstrip("x")
    match = re.match(r"[\d.]+", speed_str)
    if match:
        speed = float(match.group())
        if speed > 0:
            eta_seconds = max(0.0, (total_duration_seconds - elapsed_seconds) / speed)

    on_progress(percent, eta_seconds)
