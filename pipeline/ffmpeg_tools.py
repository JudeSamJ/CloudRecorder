"""Thin wrappers around the ffmpeg/ffprobe CLIs used for chunk reassembly.

Concat method: the concat DEMUXER with stream copy (`-f concat -safe 0 -i
list.txt -c copy`), not the concat protocol (only works for a few formats like
MPEG-TS, not MP4) and not the concat filter (decodes/re-encodes everything,
which is unnecessary and riskier for sync since all chunks in a session share
identical codec settings — they came from the same CameraX Recorder session,
just restarted per segment).
"""

import json
import shutil
import subprocess
from pathlib import Path

from pipeline.errors import FFmpegNotFoundError, ReconstructionValidationError


def check_available() -> None:
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        raise FFmpegNotFoundError(
            "ffmpeg/ffprobe not found on PATH. Install FFmpeg (https://ffmpeg.org/download.html) "
            "and ensure both ffmpeg and ffprobe are on your system PATH, then try again."
        )


def probe_duration_seconds(path: Path) -> float:
    """Returns the media duration in seconds via ffprobe, or raises
    ReconstructionValidationError if the file can't be probed (e.g. it's corrupt)."""
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "json", str(path),
        ],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise ReconstructionValidationError(
            f"ffprobe could not read {path.name}: {result.stderr.strip()}"
        )
    try:
        data = json.loads(result.stdout)
        return float(data["format"]["duration"])
    except (KeyError, ValueError, json.JSONDecodeError) as exc:
        raise ReconstructionValidationError(
            f"ffprobe returned unexpected output for {path.name}: {exc}"
        ) from exc


def write_concat_list(chunk_paths: list[Path], list_file_path: Path) -> None:
    """Writes an ffmpeg concat-demuxer list file. Paths are our own controlled
    local filenames (chunk_0001.mp4, ...), so no character-escaping is needed."""
    lines = [f"file '{path.as_posix()}'" for path in chunk_paths]
    list_file_path.write_text("\n".join(lines), encoding="utf-8")


def concat_to_master(list_file_path: Path, output_path: Path) -> None:
    """Stream-copies chunks per the concat list into a single master file.
    Raises ReconstructionValidationError with ffmpeg's stderr on failure."""
    result = subprocess.run(
        [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", str(list_file_path), "-c", "copy", str(output_path),
        ],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise ReconstructionValidationError(f"ffmpeg concat failed: {result.stderr.strip()}")


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
