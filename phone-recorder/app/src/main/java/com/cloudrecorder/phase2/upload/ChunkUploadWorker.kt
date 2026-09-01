package com.cloudrecorder.phase2.upload

import android.content.Context
import androidx.work.CoroutineWorker
import androidx.work.Data
import androidx.work.WorkerParameters
import com.cloudrecorder.phase2.EventLogger
import com.cloudrecorder.phase2.LogLevel
import java.io.File
import java.util.concurrent.TimeUnit

/**
 * Uploads exactly one chunk, resuming from wherever Drive says it left off. Runs
 * under WorkManager, which supplies: the NetworkType.CONNECTED gate (so this never
 * even starts while offline), exponential backoff between attempts, and durability
 * across process death/app restart/reboot (WorkManager persists its own queue).
 *
 * This worker is intentionally dumb about *why* it's being (re)run — whether it's the
 * first attempt, a retry after a dropped connection, or a resume after the whole app
 * was killed and relaunched, the logic is identical: ask Drive what it already has,
 * then send the rest. That uniformity is what makes "resume after app restart" not a
 * special case.
 */
class ChunkUploadWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {

    companion object {
        private const val KEY_CHUNK_ID = "chunk_id"
        private const val KEY_RESET_RETRY = "reset_retry"
        private const val MAX_RETRIES = 8
        private val SESSION_MAX_AGE_MS = TimeUnit.DAYS.toMillis(6)

        fun inputData(chunkId: String, resetRetryCount: Boolean): Data =
            Data.Builder()
                .putString(KEY_CHUNK_ID, chunkId)
                .putBoolean(KEY_RESET_RETRY, resetRetryCount)
                .build()
    }

    private val dao = UploadDatabase.get(applicationContext).chunkUploadDao()

    override suspend fun doWork(): Result {
        val chunkId = inputData.getString(KEY_CHUNK_ID) ?: return Result.failure()
        val resetRetry = inputData.getBoolean(KEY_RESET_RETRY, false)

        if (resetRetry) {
            dao.setFailure(chunkId, UploadStatus.PENDING, 0, null)
        }

        val entity = dao.getById(chunkId) ?: return Result.failure()
        if (entity.status == UploadStatus.UPLOADED) return Result.success()

        val file = File(entity.localFilePath)
        if (!file.exists()) {
            EventLogger.log(LogLevel.ERROR, "Chunk ${entity.chunkId}: local file missing, cannot upload")
            dao.setFailure(chunkId, UploadStatus.FAILED, entity.retryCount, "Local file missing")
            return Result.failure()
        }

        dao.setStatus(chunkId, UploadStatus.UPLOADING)

        val accessToken = try {
            DriveAuthManager.getAccessToken(applicationContext)
        } catch (e: DriveAuthManager.NotSignedInException) {
            dao.setFailure(chunkId, UploadStatus.PENDING, entity.retryCount, "Not signed in to Google")
            return Result.retry()
        } catch (e: DriveAuthManager.RecoverableAuthException) {
            dao.setFailure(chunkId, UploadStatus.PENDING, entity.retryCount, "Needs re-authentication — open the app")
            return Result.retry()
        } catch (e: Exception) {
            return handleTransientFailure(entity, "Auth error: ${e.message}")
        }

        val folderId = try {
            entity.driveFolderId
                ?: dao.getCachedFolderId(entity.projectName)
                ?: DriveRestClient.ensureProjectOriginalFolder(accessToken, entity.projectName).also {
                    dao.setFolderId(chunkId, it)
                }
        } catch (e: Exception) {
            return handleTransientFailure(entity, "Could not resolve Drive folder: ${e.message}")
        }

        return try {
            val driveFileId = uploadChunk(accessToken, entity, file, folderId)
            dao.markUploaded(chunkId, driveFileId)
            val deleted = file.delete()
            if (deleted) dao.markLocalFileDeleted(chunkId)
            EventLogger.log(LogLevel.INFO, "Chunk ${entity.chunkId} uploaded to Drive (id=$driveFileId)")
            Result.success()
        } catch (e: Exception) {
            handleTransientFailure(entity, "Upload failed: ${e.message}")
        }
    }

    private suspend fun uploadChunk(
        accessToken: String,
        entity: ChunkUploadEntity,
        file: File,
        folderId: String,
    ): String {
        val fileName = "${entity.sessionId}_chunk_%04d.mp4".format(entity.chunkIndex)

        val sessionAge = entity.resumableSessionCreatedAtMs?.let { System.currentTimeMillis() - it }
        var sessionUri = entity.resumableSessionUri
        if (sessionUri != null && (sessionAge == null || sessionAge > SESSION_MAX_AGE_MS)) {
            EventLogger.log(LogLevel.WARN, "Chunk ${entity.chunkId}: resumable session too old, starting fresh")
            sessionUri = null
        }

        var offset = 0L

        if (sessionUri != null) {
            when (val check = DriveRestClient.checkResumeStatus(sessionUri, entity.fileSizeBytes)) {
                is ResumeCheck.Complete -> return check.driveFileId
                is ResumeCheck.InProgress -> {
                    offset = check.bytesReceived
                    dao.setUploadedBytes(entity.chunkId, offset)
                }
                ResumeCheck.SessionInvalid -> {
                    EventLogger.log(LogLevel.WARN, "Chunk ${entity.chunkId}: resumable session expired, restarting upload")
                    sessionUri = null
                }
            }
        }

        if (sessionUri == null) {
            sessionUri = DriveRestClient.initiateResumableSession(
                accessToken = accessToken,
                fileName = fileName,
                folderId = folderId,
                fileSizeBytes = entity.fileSizeBytes,
                appProperties = mapOf(
                    "sessionId" to entity.sessionId,
                    "chunkIndex" to entity.chunkIndex.toString(),
                    "recordedAtMs" to entity.recordedAtMs.toString(),
                ),
            )
            dao.setResumableSession(entity.chunkId, sessionUri, System.currentTimeMillis())
            offset = 0L
        }

        return DriveRestClient.uploadRemaining(sessionUri, file, offset, entity.fileSizeBytes)
    }

    private suspend fun handleTransientFailure(entity: ChunkUploadEntity, message: String): Result {
        val newRetryCount = entity.retryCount + 1
        EventLogger.log(LogLevel.WARN, "Chunk ${entity.chunkId} attempt $newRetryCount failed: $message")
        return if (newRetryCount >= MAX_RETRIES) {
            dao.setFailure(entity.chunkId, UploadStatus.FAILED, newRetryCount, message)
            EventLogger.log(LogLevel.ERROR, "Chunk ${entity.chunkId} failed after $newRetryCount attempts: $message")
            Result.failure()
        } else {
            dao.setFailure(entity.chunkId, UploadStatus.PENDING, newRetryCount, message)
            Result.retry()
        }
    }
}
