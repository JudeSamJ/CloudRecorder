package com.cloudrecorder.phase2.upload

import androidx.room.Entity
import androidx.room.PrimaryKey

/**
 * One row per recording session, written once recording for that session stops
 * (RecordingService.finishStopSequence — never from an OS-kill onDestroy, since that
 * path doesn't know the true final chunk count). Drives the Phase 6 desktop
 * companion's "session complete" signal: once expectedChunkCount chunks show
 * UPLOADED, a small marker file is uploaded to Drive so the desktop app knows this
 * session's upload is genuinely finished, not just "no new chunks recently."
 */
@Entity(tableName = "sessions")
data class SessionEntity(
    @PrimaryKey val sessionId: String,
    val projectName: String,
    val expectedChunkCount: Int,
    val totalBytes: Long,
    val recordingFinishedAtMs: Long,
    val markerUploaded: Boolean = false,
    val markerDriveFileId: String? = null,
)
