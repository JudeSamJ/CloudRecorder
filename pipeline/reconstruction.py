"""Session chunk verification + master video reconstruction.

Runs entirely on the desktop (heavier, less time-sensitive than the phone-side
upload). Given a session ID, this:

  1. Finds all Drive chunks tagged with that session ID (searched globally by
     appProperties, not by project folder — the chunks' own `parents` field
     tells us which project's Original/ folder they live in).
  2. Verifies the chunk sequence has no gaps or duplicates before doing any
     work — a corrupt/incomplete sequence must fail loudly, not silently
     produce a partial or misordered master.
  3. Finds the project's existing master (one master per PROJECT, not per
     session — every new session's footage is appended to it) and, if one
     exists, confirms the new chunks' resolution/fps match it before
     attempting a stream-copy concat; a mismatch refuses cleanly rather than
     silently corrupting or re-encoding.
  4. Downloads the existing master (if any) + the new chunks to a temp
     directory, stream-copies them (existing master first, then new chunks in
     order) into one combined file via ffmpeg's concat demuxer, validates
     duration + decode integrity, and only then replaces the master's content
     in Drive (same file id — see DriveClient.update_file_content) and
     deletes the source chunks — in that order, never reversed, since the
     chunks are the safety net until the new combined master is proven.

Note the bandwidth/time tradeoff this implies: every append downloads the
*entire* existing master, not just the new footage, then re-uploads the
entire combined result. Fine for occasional short sessions; a project with
many long sessions will see each append take longer than the last.

The temp directory is always removed (success or failure) via try/finally.
"""

import shutil
import tempfile
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from pipeline import ffmpeg_tools
from pipeline.drive_client import DriveClient
from pipeline.errors import (
    MissingChunksError,
    QualityMismatchError,
    ReconstructionValidationError,
    SessionAlreadyMergedError,
    SessionNotFoundError,
)

DURATION_TOLERANCE_SECONDS = 2.0
DURATION_TOLERANCE_FRACTION = 0.02


@dataclass
class ChunkInfo:
    file_id: str
    name: str
    chunk_index: int
    recorded_at_ms: int
    size_bytes: int
    parent_id: str


@dataclass
class ReconstructionResult:
    master_name: str
    master_file_id: str
    duration_seconds: float
    size_bytes: int
    chunk_count: int
    report_file_id: str


class SessionReconstructor:
    def __init__(self, client: DriveClient | None = None, on_progress: Callable[[str], None] | None = None):
        self._client = client or DriveClient()
        self._on_progress = on_progress or (lambda _msg: None)

    def reconstruct(self, session_id: str) -> ReconstructionResult:
        ffmpeg_tools.check_available()

        self._progress(f"Looking up chunks for session '{session_id}'...")
        chunks = self._find_chunks(session_id)
        original_folder_id = chunks[0].parent_id
        project_name = self._project_name_for_folder(original_folder_id)
        master_name = f"{project_name}_master.mp4"

        report: list[str] = [
            f"Reconstruction report for session {session_id} (project '{project_name}')",
            f"Generated: {datetime.now(timezone.utc).isoformat()}",
            f"Chunks found: {len(chunks)}",
        ]
        for chunk in chunks:
            report.append(f"  chunk {chunk.chunk_index:04d}: {chunk.name} ({chunk.size_bytes} bytes, id={chunk.file_id})")

        try:
            self._verify_completeness(chunks)
        except MissingChunksError as exc:
            report.append(f"Missing chunk index(es): {exc.missing}")
            report.append(f"Duplicate chunk index(es): {exc.duplicates}")
            report.append("RECONSTRUCTION ABORTED: session incomplete — no master was created.")
            self._upload_report(report, session_id, original_folder_id)
            raise

        existing_master = self._client.find_file(master_name, original_folder_id)
        existing_props = (existing_master or {}).get("appProperties") or {}
        merged_ids = [s for s in existing_props.get("mergedSessionIds", "").split(",") if s]
        if session_id in merged_ids:
            raise SessionAlreadyMergedError(
                f"Session '{session_id}' has already been merged into '{master_name}'. "
                "No chunks remain to re-merge, and nothing was changed."
            )

        temp_dir = Path(tempfile.mkdtemp(prefix=f"cloudrecorder_reconstruct_{session_id}_"))
        try:
            segment_paths: list[Path] = []
            existing_master_path: Path | None = None
            existing_duration = 0.0

            if existing_master:
                self._progress(f"Downloading existing project master '{master_name}' to append to...")
                existing_master_path = temp_dir / f"existing_{master_name}"
                self._client.download_file(existing_master["id"], existing_master_path)
                existing_duration = ffmpeg_tools.probe_duration_seconds(existing_master_path)
                report.append(f"Existing master duration before append: {existing_duration:.2f}s")
                segment_paths.append(existing_master_path)

            new_chunk_paths = self._download_chunks(chunks, temp_dir)
            segment_paths.extend(new_chunk_paths)

            self._progress("Probing durations and resolution...")
            if existing_master_path is not None:
                self._check_quality_matches(existing_master_path, new_chunk_paths[0], master_name)

            new_durations = [ffmpeg_tools.probe_duration_seconds(path) for path in new_chunk_paths]
            expected_duration = existing_duration + sum(new_durations)
            report.append(f"Sum of new chunk durations: {sum(new_durations):.2f}s")
            report.append(f"Expected combined duration: {expected_duration:.2f}s")

            self._progress(f"Concatenating {len(segment_paths)} segment(s) with ffmpeg (stream copy)...")
            list_file = temp_dir / "concat_list.txt"
            ffmpeg_tools.write_concat_list(segment_paths, list_file)
            master_path = temp_dir / master_name
            ffmpeg_tools.concat_to_master(list_file, master_path)

            self._progress("Validating reconstructed master...")
            actual_duration = ffmpeg_tools.probe_duration_seconds(master_path)
            tolerance = max(DURATION_TOLERANCE_SECONDS, expected_duration * DURATION_TOLERANCE_FRACTION)
            duration_diff = abs(actual_duration - expected_duration)
            report.append(
                f"Master duration: {actual_duration:.2f}s (expected {expected_duration:.2f}s, "
                f"diff {duration_diff:.2f}s, tolerance {tolerance:.2f}s)"
            )
            if duration_diff > tolerance:
                report.append("Validation: FAILED (duration mismatch). Master NOT updated; chunks NOT deleted.")
                self._upload_report(report, session_id, original_folder_id)
                raise ReconstructionValidationError(
                    f"Master duration {actual_duration:.2f}s differs from the expected "
                    f"{expected_duration:.2f}s by {duration_diff:.2f}s, exceeding tolerance "
                    f"{tolerance:.2f}s. Master was NOT updated and chunks were NOT deleted."
                )

            decode_error = ffmpeg_tools.check_decodes_cleanly(master_path)
            if decode_error:
                report.append(f"Decode integrity check: FAILED — {decode_error}")
                report.append("Validation: FAILED. Master NOT updated; chunks NOT deleted.")
                self._upload_report(report, session_id, original_folder_id)
                raise ReconstructionValidationError(
                    f"Master file failed the decode integrity check: {decode_error}. "
                    "Master was NOT updated and chunks were NOT deleted."
                )
            report.append("Decode integrity check: OK")

            master_size = master_path.stat().st_size
            report.append(f"Master size: {master_size} bytes")
            report.append("Validation: PASSED")

            merged_ids.append(session_id)
            app_properties = {
                "kind": "master",
                "projectName": project_name,
                "latestSessionId": session_id,
                "mergedSessionIds": ",".join(merged_ids),
            }

            if existing_master:
                self._progress(f"Updating project master '{master_name}' in place (appending)...")
                master_file_id = self._client.update_file_content(
                    existing_master["id"], master_path,
                    progress_callback=lambda frac: self._progress(f"  upload progress: {frac * 100:.0f}%"),
                    app_properties=app_properties,
                )
                report.append(f"Updated existing master '{master_name}' in place (id={master_file_id})")
            else:
                self._progress(f"Uploading new project master '{master_name}' to Drive...")
                master_file_id = self._client.upload_file(
                    master_path, original_folder_id, master_name,
                    progress_callback=lambda frac: self._progress(f"  upload progress: {frac * 100:.0f}%"),
                    app_properties=app_properties,
                )
                report.append(f"Uploaded new master '{master_name}' (id={master_file_id})")

            self._progress(f"Deleting {len(chunks)} chunk files from Drive...")
            for chunk in chunks:
                self._client.delete_file(chunk.file_id)
            report.append(f"Deleted {len(chunks)} chunk files from Drive after confirmed master update.")

            report_file_id = self._upload_report(report, session_id, original_folder_id)
            self._progress("Done.")

            return ReconstructionResult(
                master_name=master_name,
                master_file_id=master_file_id,
                duration_seconds=actual_duration,
                size_bytes=master_size,
                chunk_count=len(chunks),
                report_file_id=report_file_id,
            )
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def _progress(self, message: str) -> None:
        self._on_progress(message)

    def _project_name_for_folder(self, original_folder_id: str) -> str:
        """original_folder_id is Content Creation/Projects/<project>/Original —
        its parent is the project folder, whose name is what we want."""
        original_folder = self._client.get_file(original_folder_id, fields="id, name, parents")
        project_folder_id = (original_folder.get("parents") or [None])[0]
        if not project_folder_id:
            raise ReconstructionValidationError(
                f"Folder '{original_folder.get('name')}' (id={original_folder_id}) has no parent project folder."
            )
        project_folder = self._client.get_file(project_folder_id, fields="id, name")
        return project_folder["name"]

    def _check_quality_matches(self, existing_master_path: Path, new_chunk_path: Path, master_name: str) -> None:
        existing_w, existing_h = ffmpeg_tools.probe_resolution(existing_master_path)
        new_w, new_h = ffmpeg_tools.probe_resolution(new_chunk_path)
        if (existing_w, existing_h) != (new_w, new_h):
            raise QualityMismatchError(
                f"Cannot append this session ({new_w}x{new_h}) to project master '{master_name}' "
                f"({existing_w}x{existing_h}) — resolution must match. Re-record at matching quality, "
                "or use a different project for this recording."
            )

    def _find_chunks(self, session_id: str) -> list[ChunkInfo]:
        fields = "id,name,size,parents,appProperties"
        raw_files = self._client.find_files_by_app_property("sessionId", session_id, fields)
        if not raw_files:
            raise SessionNotFoundError(f"No chunks found in Drive for session '{session_id}'.")

        chunks: list[ChunkInfo] = []
        for entry in raw_files:
            props = entry.get("appProperties") or {}
            name = entry.get("name", "<unknown>")
            file_id = entry["id"]

            # Phase 6's session-completion marker (and the project master/proxy,
            # which also carry a sessionId tag via latestSessionId lookups
            # elsewhere) share tags but are never chunks -- skip by kind rather
            # than treating their absent chunkIndex as corruption.
            if props.get("kind") in ("session_complete", "master", "proxy"):
                continue

            try:
                chunk_index = int(props["chunkIndex"])
            except (KeyError, ValueError) as exc:
                raise ReconstructionValidationError(
                    f"File '{name}' (id={file_id}) is tagged with sessionId '{session_id}' but has "
                    "missing/invalid chunkIndex metadata; refusing to guess its position in the sequence."
                ) from exc
            recorded_at_ms = int(props.get("recordedAtMs", 0) or 0)
            parents = entry.get("parents") or []
            if not parents:
                raise ReconstructionValidationError(f"File '{name}' (id={file_id}) has no parent folder in Drive.")
            chunks.append(
                ChunkInfo(
                    file_id=file_id,
                    name=name,
                    chunk_index=chunk_index,
                    recorded_at_ms=recorded_at_ms,
                    size_bytes=int(entry.get("size", 0) or 0),
                    parent_id=parents[0],
                )
            )

        if not chunks:
            raise SessionNotFoundError(
                f"Session '{session_id}' has a completion marker but no chunk files in Drive "
                "(already reconstructed and cleaned up?)."
            )

        parent_ids = {chunk.parent_id for chunk in chunks}
        if len(parent_ids) > 1:
            raise ReconstructionValidationError(
                f"Session '{session_id}' chunks are split across multiple Drive folders "
                f"({sorted(parent_ids)}) — refusing to guess which is authoritative."
            )

        chunks.sort(key=lambda chunk: chunk.chunk_index)
        return chunks

    def _verify_completeness(self, chunks: list[ChunkInfo]) -> None:
        indices = [chunk.chunk_index for chunk in chunks]
        counts = Counter(indices)
        duplicates = sorted(index for index, count in counts.items() if count > 1)
        max_index = max(indices)
        expected = set(range(1, max_index + 1))
        missing = sorted(expected - set(indices))

        if missing or duplicates:
            parts = []
            if missing:
                parts.append(f"missing chunk index(es) {missing}")
            if duplicates:
                parts.append(f"duplicate chunk index(es) {duplicates}")
            raise MissingChunksError("Session incomplete/corrupt: " + "; ".join(parts), missing, duplicates)

    def _download_chunks(self, chunks: list[ChunkInfo], temp_dir: Path) -> list[Path]:
        local_paths = []
        for position, chunk in enumerate(chunks, start=1):
            self._progress(f"Downloading chunk {position}/{len(chunks)}: {chunk.name}")
            local_path = temp_dir / f"chunk_{chunk.chunk_index:04d}.mp4"
            self._client.download_file(chunk.file_id, local_path)
            local_paths.append(local_path)
        return local_paths

    def _upload_report(self, lines: list[str], session_id: str, parent_folder_id: str) -> str:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        report_name = f"{session_id}_reconstruction_report_{timestamp}.txt"
        temp_path = Path(tempfile.gettempdir()) / f"{session_id}_report_{uuid.uuid4().hex}.txt"
        temp_path.write_text("\n".join(lines), encoding="utf-8")
        try:
            return self._client.upload_file(temp_path, parent_folder_id, report_name, mime_type="text/plain")
        finally:
            temp_path.unlink(missing_ok=True)
