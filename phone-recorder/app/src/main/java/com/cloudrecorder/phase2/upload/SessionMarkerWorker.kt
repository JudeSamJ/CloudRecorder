package com.cloudrecorder.phase2.upload

import android.content.Context
import androidx.work.CoroutineWorker
import androidx.work.Data
import androidx.work.WorkerParameters
import com.cloudrecorder.phase2.EventLogger
import com.cloudrecorder.phase2.LogLevel
import org.json.JSONObject

/**
 * Uploads the Phase 6 "session complete" marker once every chunk of a finished
 * recording session is confirmed UPLOADED. The Phase 6 desktop companion watches
 * for this file (tagged appProperties kind=session_complete) as its deterministic
 * signal that a session's upload is genuinely done — not just "no new chunks
 * recently," which could misfire on a long recording pause.
 */
class SessionMarkerWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {

    companion object {
        private const val KEY_SESSION_ID = "session_id"

        fun inputData(sessionId: String): Data =
            Data.Builder().putString(KEY_SESSION_ID, sessionId).build()
    }

    private val sessionDao = UploadDatabase.get(applicationContext).sessionDao()

    override suspend fun doWork(): Result {
        val sessionId = inputData.getString(KEY_SESSION_ID) ?: return Result.failure()
        val session = sessionDao.getById(sessionId) ?: return Result.failure()
        if (session.markerUploaded) return Result.success()

        // Re-verify rather than trust the caller's snapshot — this worker can run
        // well after it was enqueued (offline period, retry), so re-check completeness.
        val uploadedCount = sessionDao.countUploadedChunks(sessionId)
        if (uploadedCount < session.expectedChunkCount) return Result.failure()

        val accessToken = try {
            DriveAuthManager.getAccessToken(applicationContext)
        } catch (e: Exception) {
            EventLogger.log(LogLevel.WARN, "Session marker for $sessionId: auth not ready (${e.message}), will retry")
            return Result.retry()
        }

        return try {
            val folderId = DriveRestClient.ensureProjectOriginalFolder(accessToken, session.projectName)
            val markerJson = JSONObject().apply {
                put("sessionId", sessionId)
                put("projectName", session.projectName)
                put("chunkCount", session.expectedChunkCount)
                put("totalBytes", session.totalBytes)
                put("completedAtMs", session.recordingFinishedAtMs)
            }
            val fileId = DriveRestClient.uploadSmallFile(
                accessToken = accessToken,
                fileName = "${sessionId}_complete.json",
                folderId = folderId,
                content = markerJson.toString().toByteArray(Charsets.UTF_8),
                mimeType = "application/json",
                // Duplicated into appProperties (not just the JSON body) so the
                // Phase 6 desktop companion can discover a completed session from a
                // single files.list() call, without downloading and parsing each
                // marker's content just to learn its project name/chunk count.
                appProperties = mapOf(
                    "sessionId" to sessionId,
                    "kind" to "session_complete",
                    "projectName" to session.projectName,
                    "chunkCount" to session.expectedChunkCount.toString(),
                    "totalBytes" to session.totalBytes.toString(),
                ),
            )
            sessionDao.markMarkerUploaded(sessionId, fileId)
            EventLogger.log(LogLevel.INFO, "Session $sessionId marked complete in Drive (all $uploadedCount chunks uploaded)")
            Result.success()
        } catch (e: Exception) {
            EventLogger.log(LogLevel.WARN, "Session marker upload failed for $sessionId: ${e.message}")
            Result.retry()
        }
    }
}
