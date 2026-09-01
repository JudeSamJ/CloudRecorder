"""Tests for SessionReconstructor's pure verification/parsing logic — the
safety-critical piece that decides whether a session is complete before any
ffmpeg or Drive upload/delete work happens. A fake client stands in for
DriveClient (never constructed for real — that would need live credentials)."""

import pytest

from pipeline.errors import MissingChunksError, ReconstructionValidationError
from pipeline.reconstruction import ChunkInfo, SessionReconstructor


class FakeClient:
    def __init__(self, files):
        self._files = files

    def find_files_by_app_property(self, key, value, fields):
        return self._files


def _chunk_file(session_id, index, parent="parent1", recorded_at_ms=1000, size=500, file_id=None):
    return {
        "id": file_id or f"chunk-{index}",
        "name": f"{session_id}_chunk_{index:04d}.mp4",
        "size": str(size),
        "parents": [parent],
        "appProperties": {
            "sessionId": session_id,
            "chunkIndex": str(index),
            "recordedAtMs": str(recorded_at_ms),
        },
    }


def _reconstructor(files):
    return SessionReconstructor(client=FakeClient(files), on_progress=lambda _msg: None)


def test_verify_completeness_passes_for_gapless_sequence():
    reconstructor = _reconstructor([])
    chunks = [ChunkInfo(f"id{i}", f"n{i}", i, 0, 0, "p") for i in (1, 2, 3)]
    reconstructor._verify_completeness(chunks)  # must not raise


def test_verify_completeness_detects_missing_chunk():
    reconstructor = _reconstructor([])
    chunks = [ChunkInfo(f"id{i}", f"n{i}", i, 0, 0, "p") for i in (1, 2, 4)]
    with pytest.raises(MissingChunksError) as exc_info:
        reconstructor._verify_completeness(chunks)
    assert exc_info.value.missing == [3]
    assert exc_info.value.duplicates == []


def test_verify_completeness_detects_duplicate_chunk():
    reconstructor = _reconstructor([])
    chunks = [ChunkInfo(f"id{i}", f"n{i}", idx, 0, 0, "p") for i, idx in enumerate([1, 2, 2, 3])]
    with pytest.raises(MissingChunksError) as exc_info:
        reconstructor._verify_completeness(chunks)
    assert exc_info.value.duplicates == [2]
    assert exc_info.value.missing == []


def test_verify_completeness_detects_both_missing_and_duplicate():
    reconstructor = _reconstructor([])
    chunks = [ChunkInfo(f"id{i}", f"n{i}", idx, 0, 0, "p") for i, idx in enumerate([1, 1, 4])]
    with pytest.raises(MissingChunksError) as exc_info:
        reconstructor._verify_completeness(chunks)
    assert exc_info.value.missing == [2, 3]
    assert exc_info.value.duplicates == [1]


def test_find_chunks_sorts_by_chunk_index():
    files = [_chunk_file("S1", 3), _chunk_file("S1", 1), _chunk_file("S1", 2)]
    reconstructor = _reconstructor(files)
    chunks = reconstructor._find_chunks("S1")
    assert [c.chunk_index for c in chunks] == [1, 2, 3]


def test_find_chunks_raises_session_not_found_when_empty():
    from pipeline.errors import SessionNotFoundError

    reconstructor = _reconstructor([])
    with pytest.raises(SessionNotFoundError):
        reconstructor._find_chunks("S1")


def test_find_chunks_raises_on_missing_chunk_index_property():
    bad_file = {
        "id": "x", "name": "bad.mp4", "size": "1", "parents": ["p"],
        "appProperties": {"sessionId": "S1"},  # no chunkIndex
    }
    reconstructor = _reconstructor([bad_file])
    with pytest.raises(ReconstructionValidationError, match="chunkIndex"):
        reconstructor._find_chunks("S1")


def test_find_chunks_raises_when_split_across_multiple_parent_folders():
    files = [_chunk_file("S1", 1, parent="parentA"), _chunk_file("S1", 2, parent="parentB")]
    reconstructor = _reconstructor(files)
    with pytest.raises(ReconstructionValidationError, match="multiple Drive folders"):
        reconstructor._find_chunks("S1")


def test_find_chunks_raises_when_no_parent_folder():
    bad_file = {
        "id": "x", "name": "bad.mp4", "size": "1", "parents": [],
        "appProperties": {"sessionId": "S1", "chunkIndex": "1"},
    }
    reconstructor = _reconstructor([bad_file])
    with pytest.raises(ReconstructionValidationError, match="no parent folder"):
        reconstructor._find_chunks("S1")
