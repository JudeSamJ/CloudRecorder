package com.cloudrecorder.phase2

import androidx.camera.video.Quality
import com.cloudrecorder.phase2.upload.UploadStats
import kotlinx.coroutines.flow.MutableStateFlow

/**
 * In-process shared state between RecordingService (the source of truth, running
 * independently of the Activity's lifecycle) and MainActivity's Compose UI, which
 * just observes it. No IPC/AIDL needed since both run in the same process.
 */
object RecordingState {
    val isRecording = MutableStateFlow(false)
    val isServiceRunning = MutableStateFlow(false)

    val availableQualities = MutableStateFlow<List<Quality>>(emptyList())
    val selectedQuality = MutableStateFlow<Quality?>(null)
    val availableFrameRates = MutableStateFlow<List<Int>>(listOf(30))
    val selectedFrameRate = MutableStateFlow(30)
    val chunkIntervalSeconds = MutableStateFlow(8)
    val projectName = MutableStateFlow("")
    val signedInEmail = MutableStateFlow<String?>(null)
    val isOnline = MutableStateFlow(true)

    val elapsedMs = MutableStateFlow(0L)
    val currentChunkElapsedMs = MutableStateFlow(0L)
    val chunkCount = MutableStateFlow(0)
    val totalBytes = MutableStateFlow(0L)
    val freeBytes = MutableStateFlow(0L)

    val logEntries = MutableStateFlow<List<LogEntry>>(emptyList())
    val uploadStats = MutableStateFlow(UploadStats())

    val lastSessionSummary = MutableStateFlow<SessionSummary?>(null)

    fun resetForNewSession() {
        isRecording.value = true
        elapsedMs.value = 0L
        currentChunkElapsedMs.value = 0L
        chunkCount.value = 0
        totalBytes.value = 0L
        lastSessionSummary.value = null
    }
}

data class SessionSummary(
    val totalChunks: Int,
    val totalBytes: Long,
    val totalDurationMs: Long,
    val stoppedReason: String,
)
