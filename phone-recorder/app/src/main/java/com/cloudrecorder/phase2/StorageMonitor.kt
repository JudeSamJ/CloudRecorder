package com.cloudrecorder.phase2

import android.os.StatFs
import java.io.File

object StorageMonitor {
    const val LOW_STORAGE_WARN_BYTES = 500L * 1024 * 1024 // 500 MB
    const val CRITICAL_STORAGE_BYTES = 50L * 1024 * 1024 // 50 MB

    fun freeBytes(dir: File): Long {
        return try {
            StatFs(dir.absolutePath).availableBytes
        } catch (e: Exception) {
            EventLogger.log(LogLevel.WARN, "Could not read free storage: ${e.message}")
            -1L
        }
    }

    fun humanReadable(bytes: Long): String {
        if (bytes < 0) return "unknown"
        val kb = 1024.0
        val mb = kb * 1024
        val gb = mb * 1024
        return when {
            bytes >= gb -> String.format("%.2f GB", bytes / gb)
            bytes >= mb -> String.format("%.1f MB", bytes / mb)
            bytes >= kb -> String.format("%.0f KB", bytes / kb)
            else -> "$bytes B"
        }
    }
}
