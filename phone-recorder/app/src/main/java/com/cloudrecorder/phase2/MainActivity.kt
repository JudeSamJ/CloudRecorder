package com.cloudrecorder.phase2

import android.Manifest
import android.content.pm.PackageManager
import android.hardware.camera2.CameraCharacteristics
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.camera.camera2.interop.Camera2CameraInfo
import androidx.camera.core.CameraInfo
import androidx.camera.core.CameraSelector
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.video.QualitySelector
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.core.content.ContextCompat
import androidx.lifecycle.lifecycleScope
import com.cloudrecorder.phase2.ui.CloudRecorderTheme
import com.cloudrecorder.phase2.ui.RecorderScreen
import com.cloudrecorder.phase2.upload.DriveAuthManager
import com.cloudrecorder.phase2.upload.NetworkMonitor
import com.cloudrecorder.phase2.upload.UploadRepository
import com.google.android.gms.auth.api.signin.GoogleSignIn
import com.google.android.gms.common.api.ApiException
import kotlinx.coroutines.launch

class MainActivity : ComponentActivity() {

    // Backs the Compose UI as observable state; hasAllPermissions() alone wouldn't
    // trigger recomposition when permissions are granted via the launcher callback.
    private var permissionsGranted by mutableStateOf(false)

    private val permissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestMultiplePermissions(),
    ) { grants ->
        permissionsGranted = grants.values.all { it }
        if (permissionsGranted) {
            probeSupportedQualities()
        }
    }

    private val signInLauncher = registerForActivityResult(
        ActivityResultContracts.StartActivityForResult(),
    ) { result ->
        try {
            GoogleSignIn.getSignedInAccountFromIntent(result.data).getResult(ApiException::class.java)
            refreshSignInState()
        } catch (e: ApiException) {
            EventLogger.log(LogLevel.ERROR, "Google sign-in failed: ${e.message}")
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        permissionsGranted = hasAllPermissions()
        if (permissionsGranted) {
            probeSupportedQualities()
        }

        RecordingState.projectName.value = ProjectPrefs.load(this)
        refreshSignInState()

        val uploadRepository = UploadRepository.getInstance(this)
        lifecycleScope.launch { uploadRepository.recoverPendingUploads() }
        lifecycleScope.launch {
            uploadRepository.observeStats().collect { RecordingState.uploadStats.value = it }
        }
        lifecycleScope.launch {
            NetworkMonitor.isOnline(this@MainActivity).collect { RecordingState.isOnline.value = it }
        }

        setContent {
            CloudRecorderTheme {
                RecorderScreen(
                    hasPermissions = permissionsGranted,
                    onRequestPermissions = { permissionLauncher.launch(requiredPermissions()) },
                    onSignIn = { signInLauncher.launch(DriveAuthManager.signInClient(this).signInIntent) },
                    onProjectNameChanged = { ProjectPrefs.save(this, it) },
                )
            }
        }
    }

    override fun onResume() {
        super.onResume()
        // Catches permissions granted from system Settings while the app was backgrounded.
        if (!permissionsGranted && hasAllPermissions()) {
            permissionsGranted = true
            probeSupportedQualities()
        }
        refreshSignInState()
    }

    private fun refreshSignInState() {
        RecordingState.signedInEmail.value = DriveAuthManager.currentEmail(this)
    }

    private fun requiredPermissions(): Array<String> {
        val perms = mutableListOf(Manifest.permission.CAMERA, Manifest.permission.RECORD_AUDIO)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            perms.add(Manifest.permission.POST_NOTIFICATIONS)
        }
        return perms.toTypedArray()
    }

    private fun hasAllPermissions(): Boolean = requiredPermissions().all {
        ContextCompat.checkSelfPermission(this, it) == PackageManager.PERMISSION_GRANTED
    }

    /**
     * Queries the device's actually-supported recording qualities and frame rates up
     * front so the UI offers only real options (detecting, not hardcoding, what
     * 1080p/4K/60fps/etc. this specific device/camera can do). This does not bind a
     * use case or open the camera for capture — just reads capabilities off
     * CameraInfo/CameraCharacteristics.
     */
    private fun probeSupportedQualities() {
        val future = ProcessCameraProvider.getInstance(this)
        future.addListener({
            try {
                val provider = future.get()
                val cameraInfo = CameraSelector.DEFAULT_BACK_CAMERA
                    .filter(provider.availableCameraInfos)
                    .firstOrNull() ?: return@addListener

                val supported = QualitySelector.getSupportedQualities(cameraInfo)
                RecordingState.availableQualities.value = supported

                if (RecordingState.selectedQuality.value == null) {
                    // Default to the highest quality this device supports, per the
                    // usual recording preference — user can still pick a lower one.
                    RecordingState.selectedQuality.value = QualityUtils.sortedHighestFirst(supported).firstOrNull()
                }

                val frameRates = supportedFixedFrameRates(cameraInfo)
                RecordingState.availableFrameRates.value = frameRates
                if (RecordingState.selectedFrameRate.value !in frameRates) {
                    RecordingState.selectedFrameRate.value = frameRates.firstOrNull { it == 30 } ?: frameRates.first()
                }
            } catch (e: Exception) {
                EventLogger.log(LogLevel.ERROR, "Failed to probe supported qualities: ${e.message}")
            }
        }, ContextCompat.getMainExecutor(this))
    }

    /**
     * Fixed FPS targets (24/30/60) filtered down to only those actually within one of
     * this camera's reported CONTROL_AE_AVAILABLE_TARGET_FPS_RANGES — never offering a
     * frame rate the hardware can't actually deliver, rather than hardcoding the list.
     */
    private fun supportedFixedFrameRates(cameraInfo: CameraInfo): List<Int> {
        val candidates = listOf(24, 30, 60)
        val ranges = Camera2CameraInfo.from(cameraInfo)
            .getCameraCharacteristic(CameraCharacteristics.CONTROL_AE_AVAILABLE_TARGET_FPS_RANGES)
            ?: return listOf(30)
        val supported = candidates.filter { fps -> ranges.any { fps in it.lower..it.upper } }
        return supported.ifEmpty { listOf(30) }
    }
}
