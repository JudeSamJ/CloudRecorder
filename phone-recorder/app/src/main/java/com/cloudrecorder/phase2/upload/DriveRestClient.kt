package com.cloudrecorder.phase2.upload

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.MultipartBody
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody
import okhttp3.RequestBody.Companion.toRequestBody
import okio.BufferedSink
import org.json.JSONObject
import java.io.File
import java.io.IOException
import java.io.RandomAccessFile
import java.util.concurrent.TimeUnit

private const val DRIVE_API_BASE = "https://www.googleapis.com/drive/v3"
private const val DRIVE_UPLOAD_BASE = "https://www.googleapis.com/upload/drive/v3/files"
private const val FOLDER_MIME_TYPE = "application/vnd.google-apps.folder"

sealed class ResumeCheck {
    data class Complete(val driveFileId: String) : ResumeCheck()
    data class InProgress(val bytesReceived: Long) : ResumeCheck()
    object SessionInvalid : ResumeCheck()
}

class DriveApiException(message: String, val httpCode: Int? = null) : IOException(message)

/**
 * Direct REST implementation of the Drive v3 resumable upload protocol (not the
 * high-level Drive client library) so this app has explicit control over persisting
 * and resuming a session across process restarts — see README for the full rationale.
 */
object DriveRestClient {

    private val client = OkHttpClient.Builder()
        .connectTimeout(20, TimeUnit.SECONDS)
        .readTimeout(60, TimeUnit.SECONDS)
        .writeTimeout(60, TimeUnit.SECONDS)
        .build()

    suspend fun findFolder(accessToken: String, name: String, parentId: String?): String? =
        withContext(Dispatchers.IO) {
            val parent = parentId ?: "root"
            val query = "name = '${escape(name)}' and mimeType = '$FOLDER_MIME_TYPE' " +
                "and trashed = false and '$parent' in parents"
            val url = DRIVE_API_BASE.toHttpUrl().newBuilder()
                .addPathSegment("files")
                .addQueryParameter("q", query)
                .addQueryParameter("fields", "files(id,name)")
                .addQueryParameter("pageSize", "1")
                .build()

            val request = Request.Builder().url(url).header("Authorization", "Bearer $accessToken").get().build()
            client.newCall(request).execute().use { response ->
                if (!response.isSuccessful) throw driveError("findFolder($name)", response.code, response.body?.string())
                val files = JSONObject(response.body?.string() ?: "{}").optJSONArray("files")
                if (files != null && files.length() > 0) files.getJSONObject(0).getString("id") else null
            }
        }

    suspend fun createFolder(accessToken: String, name: String, parentId: String?): String =
        withContext(Dispatchers.IO) {
            val metadata = JSONObject().apply {
                put("name", name)
                put("mimeType", FOLDER_MIME_TYPE)
                if (parentId != null) put("parents", listOf(parentId))
            }
            val url = "$DRIVE_API_BASE/files?fields=id"
            val body = metadata.toString().toRequestBody("application/json".toMediaType())
            val request = Request.Builder().url(url)
                .header("Authorization", "Bearer $accessToken")
                .post(body)
                .build()
            client.newCall(request).execute().use { response ->
                val text = response.body?.string()
                if (!response.isSuccessful) throw driveError("createFolder($name)", response.code, text)
                JSONObject(text ?: "{}").getString("id")
            }
        }

    suspend fun ensureFolder(accessToken: String, name: String, parentId: String?): String {
        return findFolder(accessToken, name, parentId) ?: createFolder(accessToken, name, parentId)
    }

    /** Content Creation/Projects/<projectName>/Original — mirrors the Phase 1 desktop layout. */
    suspend fun ensureProjectOriginalFolder(accessToken: String, projectName: String): String {
        val root = ensureFolder(accessToken, "Content Creation", null)
        val projects = ensureFolder(accessToken, "Projects", root)
        val project = ensureFolder(accessToken, projectName, projects)
        return ensureFolder(accessToken, "Original", project)
    }

    /** POSTs metadata and returns the resumable session URI (the response's Location header). */
    suspend fun initiateResumableSession(
        accessToken: String,
        fileName: String,
        folderId: String,
        fileSizeBytes: Long,
        appProperties: Map<String, String>,
    ): String = withContext(Dispatchers.IO) {
        val metadata = JSONObject().apply {
            put("name", fileName)
            put("parents", listOf(folderId))
            put("appProperties", JSONObject(appProperties))
        }
        val url = "$DRIVE_UPLOAD_BASE?uploadType=resumable"
        val body = metadata.toString().toRequestBody("application/json; charset=UTF-8".toMediaType())
        val request = Request.Builder().url(url)
            .header("Authorization", "Bearer $accessToken")
            .header("X-Upload-Content-Type", "video/mp4")
            .header("X-Upload-Content-Length", fileSizeBytes.toString())
            .post(body)
            .build()
        client.newCall(request).execute().use { response ->
            if (!response.isSuccessful) {
                throw driveError("initiateResumableSession($fileName)", response.code, response.body?.string())
            }
            response.header("Location") ?: throw DriveApiException("No Location header in resumable init response")
        }
    }

    /**
     * Asks Drive how many bytes of this session it has actually received. Always call
     * this before resuming an upload after any interruption or process restart —
     * locally cached progress can be stale if the app died mid-write.
     */
    suspend fun checkResumeStatus(sessionUri: String, totalSize: Long): ResumeCheck =
        withContext(Dispatchers.IO) {
            val request = Request.Builder().url(sessionUri)
                .header("Content-Range", "bytes */$totalSize")
                .put(ByteArray(0).toRequestBody(null, 0, 0))
                .build()
            client.newCall(request).execute().use { response ->
                when (response.code) {
                    308 -> {
                        val range = response.header("Range")
                        val bytesReceived = range?.substringAfter('-')?.toLongOrNull()?.plus(1) ?: 0L
                        ResumeCheck.InProgress(bytesReceived)
                    }
                    200, 201 -> {
                        val id = JSONObject(response.body?.string() ?: "{}").getString("id")
                        ResumeCheck.Complete(id)
                    }
                    404, 410 -> ResumeCheck.SessionInvalid
                    else -> throw driveError("checkResumeStatus", response.code, response.body?.string())
                }
            }
        }

    /** Uploads the remaining bytes of [file] starting at [offset] in a single PUT. */
    suspend fun uploadRemaining(
        sessionUri: String,
        file: File,
        offset: Long,
        totalSize: Long,
    ): String = withContext(Dispatchers.IO) {
        val remaining = totalSize - offset
        val requestBody = object : RequestBody() {
            override fun contentType() = "video/mp4".toMediaType()
            override fun contentLength() = remaining

            // A hard seek (RandomAccessFile.seek), not InputStream.skip(), which is only
            // best-effort and could silently start the range at the wrong byte —
            // unacceptable given exact byte offsets are what keeps chunks uncorrupted.
            override fun writeTo(sink: BufferedSink) {
                RandomAccessFile(file, "r").use { raf ->
                    raf.seek(offset)
                    val buffer = ByteArray(64 * 1024)
                    var remainingToSend = remaining
                    while (remainingToSend > 0) {
                        val toRead = minOf(buffer.size.toLong(), remainingToSend).toInt()
                        val read = raf.read(buffer, 0, toRead)
                        if (read == -1) break
                        sink.write(buffer, 0, read)
                        remainingToSend -= read
                    }
                }
            }
        }

        val contentRange = if (totalSize == 0L) "bytes */0" else "bytes $offset-${totalSize - 1}/$totalSize"
        val request = Request.Builder().url(sessionUri)
            .header("Content-Range", contentRange)
            .put(requestBody)
            .build()

        client.newCall(request).execute().use { response ->
            val text = response.body?.string()
            if (!response.isSuccessful) throw driveError("uploadRemaining", response.code, text)
            JSONObject(text ?: "{}").getString("id")
        }
    }

    /**
     * Single-request multipart upload for small metadata files (Phase 6's session
     * completion marker) — the resumable protocol above exists for large video
     * chunks where a dropped connection mid-upload matters; for a few hundred bytes
     * of JSON, one multipart POST is simpler and there's nothing meaningful to
     * resume anyway.
     */
    suspend fun uploadSmallFile(
        accessToken: String,
        fileName: String,
        folderId: String,
        content: ByteArray,
        mimeType: String,
        appProperties: Map<String, String>,
    ): String = withContext(Dispatchers.IO) {
        val metadata = JSONObject().apply {
            put("name", fileName)
            put("parents", listOf(folderId))
            put("appProperties", JSONObject(appProperties))
        }
        val body = MultipartBody.Builder()
            .setType("multipart/related".toMediaType())
            .addPart(
                MultipartBody.Part.create(
                    null,
                    metadata.toString().toRequestBody("application/json; charset=UTF-8".toMediaType()),
                ),
            )
            .addPart(MultipartBody.Part.create(null, content.toRequestBody(mimeType.toMediaType())))
            .build()

        val url = "$DRIVE_UPLOAD_BASE?uploadType=multipart&fields=id"
        val request = Request.Builder().url(url)
            .header("Authorization", "Bearer $accessToken")
            .post(body)
            .build()
        client.newCall(request).execute().use { response ->
            val text = response.body?.string()
            if (!response.isSuccessful) throw driveError("uploadSmallFile($fileName)", response.code, text)
            JSONObject(text ?: "{}").getString("id")
        }
    }

    private fun escape(value: String) = value.replace("\\", "\\\\").replace("'", "\\'")

    private fun driveError(op: String, code: Int, body: String?): DriveApiException {
        val quotaLike = code == 403 || code == 429
        val suffix = if (quotaLike) " (likely rate limit/quota — will retry with backoff)" else ""
        return DriveApiException("$op failed: HTTP $code $body$suffix", code)
    }
}
