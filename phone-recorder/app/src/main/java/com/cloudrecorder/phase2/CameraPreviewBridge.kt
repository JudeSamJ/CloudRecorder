package com.cloudrecorder.phase2

import androidx.camera.core.Preview
import kotlinx.coroutines.flow.MutableStateFlow

/**
 * Hands the live CameraX Preview use case from RecordingService (which owns the
 * camera binding, since recording must survive the Activity being backgrounded)
 * to whatever UI wants to display it. The service sets this when it binds the
 * camera and clears it when it unbinds; the UI attaches/detaches a PreviewView's
 * SurfaceProvider whenever it's visible. Recording itself never depends on
 * whether anything is attached here — an unconsumed Preview use case just has no
 * frames rendered anywhere, it doesn't affect VideoCapture on the same session.
 */
object CameraPreviewBridge {
    val preview = MutableStateFlow<Preview?>(null)
}
