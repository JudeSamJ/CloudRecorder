package com.cloudrecorder.phase2

import android.content.Context
import android.util.Log
import java.io.File
import java.io.FileWriter
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.concurrent.locks.ReentrantLock
import kotlin.concurrent.withLock

enum class LogLevel { INFO, WARN, ERROR }

data class LogEntry(
    val timestampMs: Long,
    val level: LogLevel,
    val message: String,
) {
    fun formatted(): String {
        val time = SimpleDateFormat("HH:mm:ss.SSS", Locale.US).format(Date(timestampMs))
        return "[$time] ${level.name}: $message"
    }
}

/**
 * Visible-in-app event log for reliability testing (dropped frames, interruptions,
 * service kills). Also mirrors every entry to a plain-text file per session so data
 * survives even if the app process dies before you can view the in-app log.
 *
 * Capped in memory at MAX_ENTRIES; the file has no cap since a 20-30 min test at a
 * few events/minute is a trivially small text file.
 */
object EventLogger {
    private const val TAG = "CloudRecorderPhase2"
    private const val MAX_ENTRIES = 1000

    private val lock = ReentrantLock()
    private var fileWriter: FileWriter? = null

    fun startSession(context: Context, sessionDir: File) {
        lock.withLock {
            closeInternal()
            RecordingState.logEntries.value = emptyList()
            val logFile = File(sessionDir, "event_log.txt")
            fileWriter = FileWriter(logFile, true)
        }
        log(LogLevel.INFO, "Session log started: ${sessionDir.absolutePath}")
    }

    fun endSession() {
        log(LogLevel.INFO, "Session log ended")
        lock.withLock { closeInternal() }
    }

    fun log(level: LogLevel, message: String) {
        val entry = LogEntry(System.currentTimeMillis(), level, message)

        when (level) {
            LogLevel.INFO -> Log.i(TAG, message)
            LogLevel.WARN -> Log.w(TAG, message)
            LogLevel.ERROR -> Log.e(TAG, message)
        }

        val current = RecordingState.logEntries.value
        val updated = (current + entry).let {
            if (it.size > MAX_ENTRIES) it.takeLast(MAX_ENTRIES) else it
        }
        RecordingState.logEntries.value = updated

        lock.withLock {
            try {
                fileWriter?.let { writer ->
                    writer.write(entry.formatted())
                    writer.write("\n")
                    writer.flush()
                }
            } catch (e: Exception) {
                Log.e(TAG, "Failed to write log entry to file", e)
            }
        }
    }

    private fun closeInternal() {
        try {
            fileWriter?.close()
        } catch (_: Exception) {
        } finally {
            fileWriter = null
        }
    }
}
