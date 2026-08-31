package com.cloudrecorder.phase2

import androidx.camera.video.Quality

/**
 * Quality is a fixed set of constant instances (not a Kotlin enum), so we map to/from
 * short names ourselves to pass a chosen quality across the Activity -> Service
 * Intent boundary.
 */
object QualityUtils {
    private val known = listOf(Quality.UHD, Quality.FHD, Quality.HD, Quality.SD)

    fun name(quality: Quality): String = when (quality) {
        Quality.UHD -> "UHD"
        Quality.FHD -> "FHD"
        Quality.HD -> "HD"
        Quality.SD -> "SD"
        else -> "HD"
    }

    fun fromName(name: String?): Quality = when (name) {
        "UHD" -> Quality.UHD
        "FHD" -> Quality.FHD
        "HD" -> Quality.HD
        "SD" -> Quality.SD
        else -> Quality.HD
    }

    /** Orders a device's supported qualities highest-first, for display and defaulting. */
    fun sortedHighestFirst(supported: List<Quality>): List<Quality> {
        return known.filter { it in supported }
    }
}
