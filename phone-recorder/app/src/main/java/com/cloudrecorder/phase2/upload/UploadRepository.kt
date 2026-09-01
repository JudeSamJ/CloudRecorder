package com.cloudrecorder.phase2.upload

import android.content.Context
import androidx.work.BackoffPolicy
import androidx.work.Constraints
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.launch
import java.io.File
import java.util.concurrent.TimeUnit

data class UploadStats(
    val recorded: Int = 0,
    val uploaded: Int = 0,
    val pending: Int = 0,
    val uploading: Int = 0,
    val failed: Int = 0,
    val localBufferBytes: Long = 0L,
)

/**
 * The only entry point RecordingService (or anything else) needs to know about for
 * uploads: "here is a finished chunk file, get it to Drive eventually." Everything
 * about how — Drive auth, resumable sessions, retries, network gating — lives behind
 * this and the Worker it enqueues.
 */
class UploadRepository(private val context: Context) {

    private val dao = UploadDatabase.get(context).chunkUploadDao()
    private val workManager = WorkManager.getInstance(context)
    private val repositoryScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    companion object {
        @Volatile
        private var instance: UploadRepository? = null

        fun getInstance(context: Context): UploadRepository = instance ?: synchronized(this) {
            instance ?: UploadRepository(context.applicationContext).also { instance = it }
        }
    }

    fun observeAll() = dao.observeAll()

    fun observeStats(): Flow<UploadStats> = dao.observeAll().map { rows ->
        UploadStats(
            recorded = rows.size,
            uploaded = rows.count { it.status == UploadStatus.UPLOADED },
            pending = rows.count { it.status == UploadStatus.PENDING },
            uploading = rows.count { it.status == UploadStatus.UPLOADING },
            failed = rows.count { it.status == UploadStatus.FAILED },
            localBufferBytes = rows.filter { !it.localFileDeleted }.sumOf { it.fileSizeBytes },
        )
    }

    suspend fun enqueueChunk(
        sessionId: String,
        chunkIndex: Int,
        file: File,
        projectName: String,
        recordedAtMs: Long,
    ) {
        val chunkId = "${sessionId}_$chunkIndex"
        dao.insert(
            ChunkUploadEntity(
                chunkId = chunkId,
                sessionId = sessionId,
                chunkIndex = chunkIndex,
                localFilePath = file.absolutePath,
                projectName = projectName,
                fileSizeBytes = file.length(),
                recordedAtMs = recordedAtMs,
                createdAtMs = System.currentTimeMillis(),
                status = UploadStatus.PENDING,
            ),
        )
        scheduleWork(chunkId)
    }

    /**
     * Fire-and-forget variant for callers (RecordingService) that must not tie this to
     * their own lifecycle scope: if the service is killed right after a chunk
     * finalizes, a lifecycleScope-hosted coroutine would be cancelled along with it,
     * silently dropping that chunk from the queue — exactly the kind of loss this
     * phase exists to prevent. repositoryScope outlives the service.
     */
    fun enqueueChunkAsync(
        sessionId: String,
        chunkIndex: Int,
        file: File,
        projectName: String,
        recordedAtMs: Long,
    ) {
        repositoryScope.launch { enqueueChunk(sessionId, chunkIndex, file, projectName, recordedAtMs) }
    }

    /**
     * Call once on app start: finds any chunk not yet UPLOADED (from a prior session
     * that was interrupted by an app kill, crash, or phone reboot) and re-enqueues it.
     * Safe to call repeatedly — unique work names make this idempotent.
     */
    suspend fun recoverPendingUploads() {
        dao.getAllUnfinished().forEach { entity ->
            if (File(entity.localFilePath).exists()) {
                scheduleWork(entity.chunkId)
            }
        }
    }

    fun retryNow(chunkId: String) {
        scheduleWork(chunkId, resetRetryCount = true)
    }

    fun retryAllFailed() {
        repositoryScope.launch {
            dao.getFailedChunkIds().forEach { retryNow(it) }
        }
    }

    /** Deletes all queue rows. Local chunk files are deleted by the caller (the UI's
     * "Clear All Chunks" action); any in-flight worker for a now-gone chunk will find
     * no matching row and simply fail harmlessly. */
    fun clearAll() {
        repositoryScope.launch { dao.deleteAll() }
    }

    private fun scheduleWork(chunkId: String, resetRetryCount: Boolean = false) {
        val request = OneTimeWorkRequestBuilder<ChunkUploadWorker>()
            .setConstraints(
                Constraints.Builder()
                    .setRequiredNetworkType(NetworkType.CONNECTED)
                    .build(),
            )
            .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 30, TimeUnit.SECONDS)
            .setInputData(ChunkUploadWorker.inputData(chunkId, resetRetryCount))
            .build()
        workManager.enqueueUniqueWork(chunkId, ExistingWorkPolicy.KEEP, request)
    }
}
