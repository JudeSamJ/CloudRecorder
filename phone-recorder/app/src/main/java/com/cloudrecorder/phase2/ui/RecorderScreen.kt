package com.cloudrecorder.phase2.ui

import android.content.Context
import android.os.Environment
import android.widget.Toast
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.FilterChip
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.camera.video.Quality
import androidx.camera.view.PreviewView
import com.cloudrecorder.phase2.CameraPreviewBridge
import com.cloudrecorder.phase2.EventLogger
import com.cloudrecorder.phase2.LogLevel
import com.cloudrecorder.phase2.QualityUtils
import com.cloudrecorder.phase2.RecordingService
import com.cloudrecorder.phase2.RecordingState
import com.cloudrecorder.phase2.StorageMonitor
import com.cloudrecorder.phase2.upload.CreateProjectResult
import com.cloudrecorder.phase2.upload.DriveAuthManager
import com.cloudrecorder.phase2.upload.DriveRestClient
import com.cloudrecorder.phase2.upload.UploadRepository
import com.cloudrecorder.phase2.upload.UploadStats
import kotlinx.coroutines.launch
import java.io.File

@Composable
fun RecorderScreen(
    hasPermissions: Boolean,
    onRequestPermissions: () -> Unit,
    onSignIn: () -> Unit,
    onProjectNameChanged: (String) -> Unit,
) {
    val context = LocalContext.current

    LaunchedEffect(Unit) {
        if (!hasPermissions) onRequestPermissions()
    }

    Surface(modifier = Modifier.fillMaxSize()) {
        if (!hasPermissions) {
            PermissionRequiredContent(onRequestPermissions)
        } else {
            RecordingContent(context, onSignIn, onProjectNameChanged)
        }
    }
}

@Composable
private fun PermissionRequiredContent(onRequestPermissions: () -> Unit) {
    Column(
        modifier = Modifier.fillMaxSize().padding(24.dp),
        verticalArrangement = Arrangement.Center,
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Text(
            "Camera, microphone, and notification permissions are required to record.",
            style = MaterialTheme.typography.bodyLarge,
        )
        Spacer(Modifier.height(16.dp))
        Button(onClick = onRequestPermissions) { Text("Grant permissions") }
    }
}

@Composable
private fun RecordingContent(
    context: Context,
    onSignIn: () -> Unit,
    onProjectNameChanged: (String) -> Unit,
) {
    val isRecording by RecordingState.isRecording.collectAsState()
    val availableQualities by RecordingState.availableQualities.collectAsState()
    val selectedQuality by RecordingState.selectedQuality.collectAsState()
    val chunkInterval by RecordingState.chunkIntervalSeconds.collectAsState()
    val elapsedMs by RecordingState.elapsedMs.collectAsState()
    val chunkCount by RecordingState.chunkCount.collectAsState()
    val totalBytes by RecordingState.totalBytes.collectAsState()
    val freeBytes by RecordingState.freeBytes.collectAsState()
    val logEntries by RecordingState.logEntries.collectAsState()
    val summary by RecordingState.lastSessionSummary.collectAsState()
    val projectName by RecordingState.projectName.collectAsState()
    val signedInEmail by RecordingState.signedInEmail.collectAsState()
    val isOnline by RecordingState.isOnline.collectAsState()
    val uploadStats by RecordingState.uploadStats.collectAsState()

    var showClearConfirm by remember { mutableStateOf(false) }
    var isCreatingProject by remember { mutableStateOf(false) }
    val coroutineScope = rememberCoroutineScope()

    Scaffold { padding ->
        Column(
            modifier = Modifier.fillMaxSize().padding(padding).padding(16.dp),
        ) {
            Text("CloudRecorder — Phase 3", style = MaterialTheme.typography.titleLarge)
            Spacer(Modifier.height(12.dp))

            SignInRow(signedInEmail = signedInEmail, onSignIn = onSignIn)
            Spacer(Modifier.height(8.dp))

            if (!isOnline) {
                InfoBanner(
                    "Offline — chunks record and buffer locally as usual. They only " +
                        "upload (and the local buffer only stays small) while you're " +
                        "connected; uploads resume automatically once you're back online.",
                )
                Spacer(Modifier.height(8.dp))
            } else if (signedInEmail == null) {
                InfoBanner("Not signed in — recording still works, but chunks won't upload until you sign in above.")
                Spacer(Modifier.height(8.dp))
            }

            if (!isRecording) {
                OutlinedTextField(
                    value = projectName,
                    onValueChange = {
                        RecordingState.projectName.value = it
                        onProjectNameChanged(it)
                    },
                    label = { Text("Project name (matches Phase 1 Drive project)") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                )
                Spacer(Modifier.height(8.dp))
                OutlinedButton(
                    onClick = {
                        isCreatingProject = true
                        coroutineScope.launch {
                            createNewProject(context, projectName) { isCreatingProject = false }
                        }
                    },
                    enabled = !isCreatingProject && projectName.isNotBlank(),
                    modifier = Modifier.fillMaxWidth(),
                ) {
                    if (isCreatingProject) {
                        CircularProgressIndicator(modifier = Modifier.height(16.dp), strokeWidth = 2.dp)
                        Text("  Creating...")
                    } else {
                        Text("Create New Project in Drive")
                    }
                }
                Spacer(Modifier.height(12.dp))

                QualityPicker(
                    available = QualityUtils.sortedHighestFirst(availableQualities),
                    selected = selectedQuality,
                    onSelect = { RecordingState.selectedQuality.value = it },
                )
                Spacer(Modifier.height(8.dp))
                ChunkIntervalPicker(
                    selected = chunkInterval,
                    onSelect = { RecordingState.chunkIntervalSeconds.value = it },
                )
                Spacer(Modifier.height(16.dp))
            }

            if (isRecording) {
                CameraPreview(modifier = Modifier.fillMaxWidth().height(280.dp))
                Spacer(Modifier.height(12.dp))
            }

            StatsCard(
                isRecording = isRecording,
                elapsedMs = elapsedMs,
                chunkCount = chunkCount,
                totalBytes = totalBytes,
                freeBytes = freeBytes,
            )

            Spacer(Modifier.height(8.dp))
            UploadStatsCard(uploadStats)

            Spacer(Modifier.height(12.dp))

            Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                if (!isRecording) {
                    Button(
                        onClick = {
                            val quality = selectedQuality ?: Quality.HD
                            context.startForegroundService(
                                RecordingService.startIntent(context, quality, chunkInterval, projectName),
                            )
                        },
                        enabled = selectedQuality != null && projectName.isNotBlank(),
                    ) { Text("Start Recording") }
                } else {
                    Button(onClick = {
                        context.startForegroundService(RecordingService.stopIntent(context))
                    }) { Text("Stop Recording") }
                }

                OutlinedButton(
                    onClick = { showClearConfirm = true },
                    enabled = !isRecording,
                ) { Text("Clear All Chunks") }
            }

            summary?.let {
                Spacer(Modifier.height(12.dp))
                SummaryCard(it.totalChunks, it.totalBytes, it.totalDurationMs, it.stoppedReason)
            }

            Spacer(Modifier.height(16.dp))
            Text("Event Log", style = MaterialTheme.typography.titleMedium)
            Spacer(Modifier.height(4.dp))
            LogList(
                entries = logEntries.map { it.formatted() to it.level },
                modifier = Modifier.weight(1f),
            )
        }
    }

    if (showClearConfirm) {
        AlertDialog(
            onDismissRequest = { showClearConfirm = false },
            title = { Text("Clear all recorded chunks?") },
            text = { Text("This deletes every chunk file from all sessions on this device. This cannot be undone.") },
            confirmButton = {
                Button(onClick = {
                    clearAllChunks(context)
                    showClearConfirm = false
                }) { Text("Delete") }
            },
            dismissButton = {
                OutlinedButton(onClick = { showClearConfirm = false }) { Text("Cancel") }
            },
        )
    }
}

/**
 * Live feed from RecordingService's camera binding, attached via CameraPreviewBridge.
 * Recording itself runs entirely in the service regardless of whether this is ever
 * shown — this just gives the Preview use case a Surface to render into while the
 * Activity is visible; nothing here affects background recording reliability.
 */
@Composable
private fun CameraPreview(modifier: Modifier = Modifier) {
    val previewUseCase by CameraPreviewBridge.preview.collectAsState()
    val context = LocalContext.current
    val previewView = remember {
        PreviewView(context).apply { implementationMode = PreviewView.ImplementationMode.PERFORMANCE }
    }

    AndroidView(modifier = modifier, factory = { previewView })

    LaunchedEffect(previewUseCase) {
        previewUseCase?.setSurfaceProvider(previewView.surfaceProvider)
    }

    DisposableEffect(Unit) {
        onDispose { previewUseCase?.setSurfaceProvider(null) }
    }
}

private suspend fun createNewProject(context: Context, projectName: String, onDone: () -> Unit) {
    try {
        val accessToken = DriveAuthManager.getAccessToken(context)
        when (val result = DriveRestClient.createProjectStructure(accessToken, projectName)) {
            is CreateProjectResult.Created -> {
                Toast.makeText(context, "Created project '$projectName' in Drive", Toast.LENGTH_SHORT).show()
                EventLogger.log(LogLevel.INFO, "Created new project '$projectName' in Drive (folder id=${result.projectFolderId})")
            }
            CreateProjectResult.AlreadyExists -> {
                Toast.makeText(context, "Project '$projectName' already exists", Toast.LENGTH_SHORT).show()
                EventLogger.log(LogLevel.WARN, "Create project: '$projectName' already exists in Drive")
            }
        }
    } catch (e: DriveAuthManager.NotSignedInException) {
        Toast.makeText(context, "Sign in with Google first", Toast.LENGTH_SHORT).show()
        EventLogger.log(LogLevel.ERROR, "Create project failed: not signed in")
    } catch (e: DriveAuthManager.RecoverableAuthException) {
        Toast.makeText(context, "Needs re-authentication — sign in again", Toast.LENGTH_SHORT).show()
        EventLogger.log(LogLevel.ERROR, "Create project failed: needs re-authentication")
    } catch (e: Exception) {
        Toast.makeText(context, "Create project failed: ${e.message}", Toast.LENGTH_LONG).show()
        EventLogger.log(LogLevel.ERROR, "Create project '$projectName' failed: ${e.message}")
    } finally {
        onDone()
    }
}

private fun clearAllChunks(context: Context) {
    val root = File(context.getExternalFilesDir(Environment.DIRECTORY_MOVIES), "sessions")
    root.deleteRecursively()
    UploadRepository.getInstance(context).clearAll()
    RecordingState.chunkCount.value = 0
    RecordingState.totalBytes.value = 0L
    RecordingState.lastSessionSummary.value = null
    RecordingState.logEntries.value = emptyList()
    EventLogger.log(LogLevel.INFO, "Cleared all recorded chunks and upload queue from device storage")
}

@Composable
private fun SignInRow(signedInEmail: String?, onSignIn: () -> Unit) {
    Row(
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        if (signedInEmail != null) {
            Text("Signed in as $signedInEmail", style = MaterialTheme.typography.bodySmall)
        } else {
            OutlinedButton(onClick = onSignIn) { Text("Sign in with Google") }
        }
    }
}

@Composable
private fun InfoBanner(text: String) {
    Card(
        modifier = Modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.secondaryContainer),
    ) {
        Text(
            text,
            style = MaterialTheme.typography.bodySmall,
            modifier = Modifier.padding(12.dp),
        )
    }
}

@Composable
private fun UploadStatsCard(stats: UploadStats) {
    val context = LocalContext.current
    Card(modifier = Modifier.fillMaxWidth()) {
        Column(modifier = Modifier.padding(16.dp)) {
            Text("Upload status", style = MaterialTheme.typography.titleMedium)
            Spacer(Modifier.height(4.dp))
            Text("Recorded: ${stats.recorded}   Uploaded: ${stats.uploaded}")
            Text("Uploading: ${stats.uploading}   Pending: ${stats.pending}   Failed: ${stats.failed}")
            Text("Local buffer (not yet uploaded): ${StorageMonitor.humanReadable(stats.localBufferBytes)}")
            if (stats.failed > 0) {
                Spacer(Modifier.height(8.dp))
                OutlinedButton(onClick = { UploadRepository.getInstance(context).retryAllFailed() }) {
                    Text("Retry ${stats.failed} failed chunk(s)")
                }
            }
        }
    }
}

@Composable
private fun QualityPicker(available: List<Quality>, selected: Quality?, onSelect: (Quality) -> Unit) {
    Column {
        Text("Quality (detected on this device)", style = MaterialTheme.typography.labelLarge)
        Spacer(Modifier.height(4.dp))
        if (available.isEmpty()) {
            Text("Detecting supported qualities...", style = MaterialTheme.typography.bodySmall)
        } else {
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                available.forEach { quality ->
                    FilterChip(
                        selected = quality == selected,
                        onClick = { onSelect(quality) },
                        label = { Text(QualityUtils.name(quality)) },
                    )
                }
            }
        }
    }
}

@Composable
private fun ChunkIntervalPicker(selected: Int, onSelect: (Int) -> Unit) {
    Column {
        Text("Chunk length", style = MaterialTheme.typography.labelLarge)
        Spacer(Modifier.height(4.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            listOf(5, 8, 10).forEach { seconds ->
                FilterChip(
                    selected = seconds == selected,
                    onClick = { onSelect(seconds) },
                    label = { Text("${seconds}s") },
                )
            }
        }
    }
}

@Composable
private fun StatsCard(
    isRecording: Boolean,
    elapsedMs: Long,
    chunkCount: Int,
    totalBytes: Long,
    freeBytes: Long,
) {
    Card(modifier = Modifier.fillMaxWidth()) {
        Column(modifier = Modifier.padding(16.dp)) {
            val elapsedSec = elapsedMs / 1000
            Text(
                if (isRecording) "Recording: ${elapsedSec / 60}m ${elapsedSec % 60}s" else "Not recording",
                style = MaterialTheme.typography.titleMedium,
            )
            Spacer(Modifier.height(4.dp))
            Text("Chunks written: $chunkCount")
            Text("Total size: ${StorageMonitor.humanReadable(totalBytes)}")
            Text("Free storage: ${StorageMonitor.humanReadable(freeBytes)}")
        }
    }
}

@Composable
private fun SummaryCard(chunks: Int, bytes: Long, durationMs: Long, reason: String) {
    Card(modifier = Modifier.fillMaxWidth()) {
        Column(modifier = Modifier.padding(16.dp)) {
            Text("Last session summary", style = MaterialTheme.typography.titleMedium)
            Spacer(Modifier.height(4.dp))
            Text("Chunks: $chunks")
            Text("Total size: ${StorageMonitor.humanReadable(bytes)}")
            Text("Duration: ${durationMs / 1000}s")
            Text("Stopped reason: $reason")
        }
    }
}

@Composable
private fun LogList(entries: List<Pair<String, LogLevel>>, modifier: Modifier = Modifier) {
    val listState = rememberLazyListState()
    LaunchedEffect(entries.size) {
        if (entries.isNotEmpty()) listState.animateScrollToItem(entries.size - 1)
    }
    LazyColumn(
        state = listState,
        modifier = modifier.fillMaxWidth(),
        contentPadding = PaddingValues(vertical = 4.dp),
    ) {
        items(entries) { (text, level) ->
            val color = when (level) {
                LogLevel.ERROR -> Color(0xFFB00020)
                LogLevel.WARN -> Color(0xFFB26A00)
                LogLevel.INFO -> MaterialTheme.colorScheme.onSurfaceVariant
            }
            Text(
                text,
                color = color,
                style = MaterialTheme.typography.bodySmall,
                modifier = Modifier.fillMaxWidth().padding(vertical = 2.dp),
            )
        }
    }
}
