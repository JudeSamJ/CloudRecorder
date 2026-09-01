package com.cloudrecorder.phase2.upload

import androidx.room.Entity
import androidx.room.PrimaryKey

enum class UploadStatus { PENDING, UPLOADING, UPLOADED, FAILED }

/**
 * One row per recorded chunk file. chunkId is deterministic ("<sessionId>_<chunkIndex>")
 * so it doubles as the WorkManager unique work name — that uniqueness (with
 * ExistingWorkPolicy.KEEP) is what prevents the same chunk from ever being enqueued,
 * and therefore uploaded, twice.
 *
 * Rows are never deleted after a successful upload (kept as an audit trail for Phase 4
 * completeness checks); only the local video file is deleted once status = UPLOADED.
 */
@Entity(tableName = "chunk_uploads")
data class ChunkUploadEntity(
    @PrimaryKey val chunkId: String,
    val sessionId: String,
    val chunkIndex: Int,
    val localFilePath: String,
    val projectName: String,
    val fileSizeBytes: Long,
    val recordedAtMs: Long,
    val createdAtMs: Long,
    val status: UploadStatus,
    val driveFolderId: String? = null,
    val resumableSessionUri: String? = null,
    val resumableSessionCreatedAtMs: Long? = null,
    val uploadedBytes: Long = 0L,
    val retryCount: Int = 0,
    val lastErrorMessage: String? = null,
    val driveFileId: String? = null,
    val localFileDeleted: Boolean = false,
)
