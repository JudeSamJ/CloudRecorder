package com.cloudrecorder.phase2

import android.Manifest
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import android.os.Environment
import android.os.PowerManager
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat
import androidx.core.content.ContextCompat
import androidx.camera.core.CameraSelector
import androidx.camera.core.Preview
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.video.FallbackStrategy
import androidx.camera.video.FileOutputOptions
import androidx.camera.video.Quality
import androidx.camera.video.QualitySelector
import androidx.camera.video.Recorder
import androidx.camera.video.Recording
import androidx.camera.video.VideoCapture
import androidx.camera.video.VideoRecordEvent
import androidx.lifecycle.LifecycleService
import androidx.lifecycle.lifecycleScope
import com.cloudrecorder.phase2.upload.UploadRepository
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * Foreground service owning the camera and the chunked-recording loop.
 *
 * Design notes (see README for the full rationale):
 * - Chunking uses plain sequential finalized MP4 files, one per segment, rather than
 *   a single fragmented-MP4 stream: CameraX's high-level Recorder API is built around
 *   independent start/stop/finalize cycles, and this phase's whole purpose is to
 *   measure the reliability (including chunk-boundary gaps) of background recording,
 *   not to hide it behind a continuous stream.
 * - START_NOT_STICKY is intentional: if the OS kills this service, we want that to be
 *   an observable, logged event (visible next time the app is opened), not silently
 *   papered over by an automatic restart that would corrupt the reliability data this
 *   phase exists to collect.
 */
class RecordingService : LifecycleService() {

    companion object {
        const val ACTION_START = "com.cloudrecorder.phase2.action.START"
        const val ACTION_STOP = "com.cloudrecorder.phase2.action.STOP"
        const val EXTRA_QUALITY_NAME = "quality_name"
        const val EXTRA_CHUNK_INTERVAL_SECONDS = "chunk_interval_seconds"
        const val EXTRA_PROJECT_NAME = "project_name"

        private const val CHANNEL_ID = "recording_channel"
        private const val NOTIFICATION_ID = 1001
        private const val GAP_WARN_THRESHOLD_MS = 300L

        fun startIntent(context: Context, quality: Quality, chunkIntervalSeconds: Int, projectName: String): Intent =
            Intent(context, RecordingService::class.java).apply {
                action = ACTION_START
                putExtra(EXTRA_QUALITY_NAME, QualityUtils.name(quality))
                putExtra(EXTRA_CHUNK_INTERVAL_SECONDS, chunkIntervalSeconds)
                putExtra(EXTRA_PROJECT_NAME, projectName)
            }

        fun stopIntent(context: Context): Intent =
            Intent(context, RecordingService::class.java).apply { action = ACTION_STOP }
    }

    private var cameraProvider: ProcessCameraProvider? = null
    private var videoCapture: VideoCapture<Recorder>? = null
    private var currentRecording: Recording? = null
    private var wakeLock: PowerManager.WakeLock? = null
    private var tickerJob: Job? = null

    private lateinit var sessionDir: File
    private lateinit var sessionId: String
    private lateinit var projectName: String
    private var sessionStartMs = 0L
    private var chunkIndex = 0
    private var totalBytesAccum = 0L
    private var lastStopTimestamp = 0L
    private var isFullyStopping = false
    private var stopReason = "unknown"
    private var currentChunkFile: File? = null
    private var currentChunkRecordedAtMs = 0L

    private val uploadRepository by lazy { UploadRepository.getInstance(applicationContext) }

    override fun onCreate() {
        super.onCreate()
        createNotificationChannel()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        super.onStartCommand(intent, flags, startId)
        // The platform can redeliver onStartCommand with a null intent (e.g. after
        // the system restarts the service); we don't request a sticky restart
        // (START_NOT_STICKY below) specifically so this shouldn't happen in
        // practice, but guard it anyway rather than crash on a null deref.
        if (intent == null) return START_NOT_STICKY
        when (intent.action) {
            ACTION_START -> handleStartAction(intent)
            ACTION_STOP -> requestFullStop("user_requested")
        }
        return START_NOT_STICKY
    }

    private fun handleStartAction(intent: Intent) {
        if (RecordingState.isServiceRunning.value) return

        val quality = QualityUtils.fromName(intent.getStringExtra(EXTRA_QUALITY_NAME))
        val intervalSeconds = intent.getIntExtra(EXTRA_CHUNK_INTERVAL_SECONDS, 8)
        projectName = intent.getStringExtra(EXTRA_PROJECT_NAME)?.trim().takeUnless { it.isNullOrEmpty() }
            ?: "Untitled"
        RecordingState.chunkIntervalSeconds.value = intervalSeconds

        sessionStartMs = System.currentTimeMillis()
        sessionDir = createSessionDir()
        sessionId = sessionDir.name
        chunkIndex = 0
        totalBytesAccum = 0L
        lastStopTimestamp = 0L
        isFullyStopping = false
        stopReason = "unknown"

        EventLogger.startSession(this, sessionDir)
        RecordingState.resetForNewSession()
        RecordingState.isServiceRunning.value = true

        acquireWakeLock()
        startForegroundNotification()
        startTicker(intervalSeconds)
        initCameraAndBegin(quality)
    }

    private fun createSessionDir(): File {
        val root = File(getExternalFilesDir(Environment.DIRECTORY_MOVIES), "sessions")
        val name = SimpleDateFormat("yyyyMMdd_HHmmss", Locale.US).format(Date())
        val dir = File(root, name)
        dir.mkdirs()
        return dir
    }

    private fun acquireWakeLock() {
        val pm = getSystemService(Context.POWER_SERVICE) as PowerManager
        wakeLock = pm.newWakeLock(
            PowerManager.PARTIAL_WAKE_LOCK,
            "CloudRecorderPhase2::RecordingWakeLock",
        ).apply {
            setReferenceCounted(false)
            acquire(10 * 60 * 60 * 1000L) // 10h safety cap, released explicitly on stop
        }
    }

    private fun startForegroundNotification() {
        val notification = buildNotification("Starting...", 0, 0)
        ServiceCompat.startForeground(
            this,
            NOTIFICATION_ID,
            notification,
            ServiceInfo.FOREGROUND_SERVICE_TYPE_CAMERA or ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE,
        )
    }

    private fun buildNotification(status: String, elapsedMs: Long, chunks: Int): Notification {
        val elapsedSec = elapsedMs / 1000
        val text = "$status  ${elapsedSec / 60}m${elapsedSec % 60}s  $chunks chunks"
        val openAppIntent = packageManager.getLaunchIntentForPackage(packageName)
        val pendingIntent = openAppIntent?.let {
            PendingIntent.getActivity(
                this, 0, it,
                PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
            )
        }
        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle(getString(R.string.app_name))
            .setContentText(text)
            .setSmallIcon(android.R.drawable.ic_menu_camera)
            .setOngoing(true)
            .setContentIntent(pendingIntent)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .build()
    }

    private fun updateNotification() {
        val manager = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        val notification = buildNotification(
            "Recording",
            RecordingState.elapsedMs.value,
            RecordingState.chunkCount.value,
        )
        manager.notify(NOTIFICATION_ID, notification)
    }

    private fun startTicker(intervalSeconds: Int) {
        tickerJob?.cancel()
        tickerJob = lifecycleScope.launch {
            while (isActive) {
                delay(1000)
                if (!RecordingState.isRecording.value) continue
                RecordingState.elapsedMs.value = System.currentTimeMillis() - sessionStartMs
                RecordingState.freeBytes.value = StorageMonitor.freeBytes(sessionDir)
                updateNotification()
            }
        }
    }

    private fun initCameraAndBegin(requestedQuality: Quality) {
        val future = ProcessCameraProvider.getInstance(this)
        future.addListener({
            try {
                val provider = future.get()
                cameraProvider = provider

                val cameraInfo = CameraSelector.DEFAULT_BACK_CAMERA
                    .filter(provider.availableCameraInfos)
                    .firstOrNull()

                if (cameraInfo == null) {
                    EventLogger.log(LogLevel.ERROR, "No back camera available on this device")
                    requestFullStop("no_camera")
                    return@addListener
                }

                val supported = QualitySelector.getSupportedQualities(cameraInfo)
                RecordingState.availableQualities.value = supported

                val effectiveQuality = if (requestedQuality in supported) requestedQuality
                else supported.firstOrNull() ?: Quality.HIGHEST

                val qualitySelector = QualitySelector.from(
                    effectiveQuality,
                    FallbackStrategy.higherQualityOrLowerThan(effectiveQuality),
                )
                val recorder = Recorder.Builder().setQualitySelector(qualitySelector).build()
                val capture = VideoCapture.withOutput(recorder)
                videoCapture = capture

                // Bound in the same bindToLifecycle call as the recording use case, on
                // the same camera session — this does not affect VideoCapture whether
                // or not anything is actually attached to it as a frame consumer.
                val previewUseCase = Preview.Builder().build()

                provider.unbindAll()
                provider.bindToLifecycle(this, CameraSelector.DEFAULT_BACK_CAMERA, capture, previewUseCase)
                CameraPreviewBridge.preview.value = previewUseCase

                EventLogger.log(
                    LogLevel.INFO,
                    "Camera bound. Requested quality=${QualityUtils.name(requestedQuality)}, " +
                        "using=${QualityUtils.name(effectiveQuality)}, " +
                        "device supports=${supported.joinToString { QualityUtils.name(it) }}",
                )

                startNewSegment()
            } catch (e: Exception) {
                EventLogger.log(LogLevel.ERROR, "Camera initialization failed: ${e.message}")
                requestFullStop("camera_init_failed")
            }
        }, ContextCompat.getMainExecutor(this))
    }

    private fun startNewSegment() {
        val capture = videoCapture ?: return
        chunkIndex += 1
        val file = File(sessionDir, "chunk_%04d.mp4".format(chunkIndex))
        currentChunkFile = file
        currentChunkRecordedAtMs = System.currentTimeMillis()
        val outputOptions = FileOutputOptions.Builder(file).build()

        var pending = capture.output.prepareRecording(this, outputOptions)
        val hasAudioPermission = ContextCompat.checkSelfPermission(
            this,
            Manifest.permission.RECORD_AUDIO,
        ) == PackageManager.PERMISSION_GRANTED
        if (hasAudioPermission) {
            pending = pending.withAudioEnabled()
        }

        currentRecording = pending.start(ContextCompat.getMainExecutor(this)) { event ->
            handleVideoEvent(event)
        }

        scheduleRotation()
    }

    private fun scheduleRotation() {
        val intervalMs = RecordingState.chunkIntervalSeconds.value * 1000L
        lifecycleScope.launch {
            delay(intervalMs)
            if (!isFullyStopping && RecordingState.isRecording.value) {
                lastStopTimestamp = System.currentTimeMillis()
                currentRecording?.stop()
            }
        }
    }

    private fun handleVideoEvent(event: VideoRecordEvent) {
        when (event) {
            is VideoRecordEvent.Start -> {
                if (lastStopTimestamp > 0) {
                    val gap = System.currentTimeMillis() - lastStopTimestamp
                    if (gap > GAP_WARN_THRESHOLD_MS) {
                        EventLogger.log(
                            LogLevel.WARN,
                            "Chunk $chunkIndex started after a ${gap}ms gap (possible frame loss at boundary)",
                        )
                    } else {
                        EventLogger.log(LogLevel.INFO, "Chunk $chunkIndex started (gap ${gap}ms)")
                    }
                } else {
                    EventLogger.log(LogLevel.INFO, "Chunk $chunkIndex started")
                }
            }

            is VideoRecordEvent.Finalize -> {
                val stats = event.recordingStats
                totalBytesAccum += stats.numBytesRecorded
                RecordingState.chunkCount.value = chunkIndex
                RecordingState.totalBytes.value = totalBytesAccum

                if (event.hasError()) {
                    EventLogger.log(
                        LogLevel.ERROR,
                        "Chunk $chunkIndex finalize error (code ${event.error}): ${event.cause?.message}",
                    )
                } else {
                    EventLogger.log(
                        LogLevel.INFO,
                        "Chunk $chunkIndex finalized: ${StorageMonitor.humanReadable(stats.numBytesRecorded)}, " +
                            "${stats.recordedDurationNanos / 1_000_000}ms",
                    )
                }

                // Even an error'd finalize (e.g. ERROR_SOURCE_INACTIVE) can leave a
                // playable, non-empty file up to the point of failure — enqueue it
                // rather than silently discarding footage that was actually captured.
                val finishedFile = currentChunkFile
                if (finishedFile != null && finishedFile.exists() && finishedFile.length() > 0) {
                    uploadRepository.enqueueChunkAsync(sessionId, chunkIndex, finishedFile, projectName, currentChunkRecordedAtMs)
                }

                if (isFullyStopping) {
                    finishStopSequence()
                } else {
                    checkStorageAndContinue()
                }
            }

            is VideoRecordEvent.Pause -> EventLogger.log(
                LogLevel.WARN,
                "Recording paused unexpectedly during chunk $chunkIndex",
            )

            is VideoRecordEvent.Resume -> EventLogger.log(
                LogLevel.INFO,
                "Recording resumed during chunk $chunkIndex",
            )

            is VideoRecordEvent.Status -> {
                RecordingState.currentChunkElapsedMs.value =
                    event.recordingStats.recordedDurationNanos / 1_000_000
            }
        }
    }

    private fun checkStorageAndContinue() {
        val free = StorageMonitor.freeBytes(sessionDir)
        RecordingState.freeBytes.value = free

        if (free in 0 until StorageMonitor.CRITICAL_STORAGE_BYTES) {
            EventLogger.log(
                LogLevel.ERROR,
                "Critical low storage (${StorageMonitor.humanReadable(free)}); stopping recording automatically",
            )
            requestFullStop("critical_low_storage")
            return
        }
        if (free in 0 until StorageMonitor.LOW_STORAGE_WARN_BYTES) {
            EventLogger.log(LogLevel.WARN, "Low storage remaining: ${StorageMonitor.humanReadable(free)}")
        }

        startNewSegment()
    }

    private fun requestFullStop(reason: String) {
        if (isFullyStopping) return
        isFullyStopping = true
        stopReason = reason
        if (currentRecording != null) {
            currentRecording?.stop()
        } else {
            finishStopSequence()
        }
    }

    private fun finishStopSequence() {
        val summary = SessionSummary(
            totalChunks = chunkIndex,
            totalBytes = totalBytesAccum,
            totalDurationMs = System.currentTimeMillis() - sessionStartMs,
            stoppedReason = stopReason,
        )
        RecordingState.lastSessionSummary.value = summary
        RecordingState.isRecording.value = false
        RecordingState.isServiceRunning.value = false

        EventLogger.log(
            LogLevel.INFO,
            "Recording stopped (reason=$stopReason). chunks=${summary.totalChunks}, " +
                "size=${StorageMonitor.humanReadable(summary.totalBytes)}, " +
                "duration=${summary.totalDurationMs / 1000}s",
        )
        EventLogger.endSession()

        // Phase 6 completion signal: only from this normal-stop path, never from the
        // OS-kill branch in onDestroy() below, which doesn't know the true final
        // chunk count.
        if (chunkIndex > 0) {
            uploadRepository.markSessionRecordingComplete(sessionId, projectName, chunkIndex, totalBytesAccum)
        }

        tickerJob?.cancel()
        cameraProvider?.unbindAll()
        CameraPreviewBridge.preview.value = null
        releaseWakeLock()

        ServiceCompat.stopForeground(this, ServiceCompat.STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    private fun releaseWakeLock() {
        try {
            wakeLock?.let { if (it.isHeld) it.release() }
        } catch (_: Exception) {
        } finally {
            wakeLock = null
        }
    }

    override fun onTaskRemoved(rootIntent: Intent?) {
        if (RecordingState.isRecording.value) {
            EventLogger.log(
                LogLevel.WARN,
                "onTaskRemoved: app swiped away from Recents while recording " +
                    "(foreground service should keep running independently)",
            )
        }
        super.onTaskRemoved(rootIntent)
    }

    override fun onDestroy() {
        if (RecordingState.isRecording.value) {
            EventLogger.log(
                LogLevel.ERROR,
                "Service onDestroy() called while still recording — this was NOT a user-requested " +
                    "stop, so the OS/battery manager likely killed the service. Chunks completed so " +
                    "far: $chunkIndex",
            )
            RecordingState.isRecording.value = false
            RecordingState.lastSessionSummary.value = SessionSummary(
                totalChunks = chunkIndex,
                totalBytes = totalBytesAccum,
                totalDurationMs = System.currentTimeMillis() - sessionStartMs,
                stoppedReason = "service_killed_by_os",
            )
            EventLogger.endSession()
        }
        RecordingState.isServiceRunning.value = false
        tickerJob?.cancel()
        CameraPreviewBridge.preview.value = null
        releaseWakeLock()
        super.onDestroy()
    }

    private fun createNotificationChannel() {
        val manager = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        val channel = NotificationChannel(
            CHANNEL_ID,
            getString(R.string.notification_channel_name),
            NotificationManager.IMPORTANCE_LOW,
        ).apply {
            description = getString(R.string.notification_channel_description)
        }
        manager.createNotificationChannel(channel)
    }
}
