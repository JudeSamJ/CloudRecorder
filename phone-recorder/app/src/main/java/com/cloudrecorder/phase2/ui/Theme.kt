package com.cloudrecorder.phase2.ui

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

/**
 * Nothing in the app previously wrapped its content in a MaterialTheme at all, so
 * every MaterialTheme.colorScheme.* reference (throughout RecorderScreen) was
 * silently falling back to Compose Material3's default light color scheme,
 * regardless of the device's system theme setting. This is a fixed dark scheme
 * (not "follow system"), per what was asked for.
 */
private val CloudRecorderDarkColors = darkColorScheme(
    primary = Color(0xFFB9A9FF),
    onPrimary = Color(0xFF2A1D63),
    secondaryContainer = Color(0xFF3A3560),
    onSecondaryContainer = Color(0xFFE3DEFF),
    background = Color(0xFF121016),
    onBackground = Color(0xFFE7E1EC),
    surface = Color(0xFF1C1A22),
    onSurface = Color(0xFFE7E1EC),
    surfaceVariant = Color(0xFF2B2830),
    onSurfaceVariant = Color(0xFFCAC4CF),
)

@Composable
fun CloudRecorderTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = CloudRecorderDarkColors,
        content = content,
    )
}
