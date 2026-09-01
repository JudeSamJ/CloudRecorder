package com.cloudrecorder.phase2.upload

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.Query
import androidx.room.Update
import kotlinx.coroutines.flow.Flow

@Dao
interface ChunkUploadDao {

    @Insert(onConflict = OnConflictStrategy.IGNORE)
    suspend fun insert(entity: ChunkUploadEntity): Long

    @Update
    suspend fun update(entity: ChunkUploadEntity)

    @Query("SELECT * FROM chunk_uploads WHERE chunkId = :chunkId")
    suspend fun getById(chunkId: String): ChunkUploadEntity?

    @Query("SELECT * FROM chunk_uploads WHERE status != 'UPLOADED' ORDER BY sessionId, chunkIndex")
    suspend fun getAllUnfinished(): List<ChunkUploadEntity>

    @Query("SELECT * FROM chunk_uploads ORDER BY createdAtMs DESC")
    fun observeAll(): Flow<List<ChunkUploadEntity>>

    @Query("UPDATE chunk_uploads SET status = :status WHERE chunkId = :chunkId")
    suspend fun setStatus(chunkId: String, status: UploadStatus)

    @Query(
        "UPDATE chunk_uploads SET status = :status, retryCount = :retryCount, " +
            "lastErrorMessage = :errorMessage WHERE chunkId = :chunkId",
    )
    suspend fun setFailure(chunkId: String, status: UploadStatus, retryCount: Int, errorMessage: String?)

    @Query(
        "UPDATE chunk_uploads SET resumableSessionUri = :uri, resumableSessionCreatedAtMs = :createdAtMs " +
            "WHERE chunkId = :chunkId",
    )
    suspend fun setResumableSession(chunkId: String, uri: String?, createdAtMs: Long?)

    @Query("UPDATE chunk_uploads SET uploadedBytes = :bytes WHERE chunkId = :chunkId")
    suspend fun setUploadedBytes(chunkId: String, bytes: Long)

    @Query(
        "UPDATE chunk_uploads SET status = 'UPLOADED', driveFileId = :driveFileId, " +
            "uploadedBytes = fileSizeBytes WHERE chunkId = :chunkId",
    )
    suspend fun markUploaded(chunkId: String, driveFileId: String)

    @Query("UPDATE chunk_uploads SET localFileDeleted = 1 WHERE chunkId = :chunkId")
    suspend fun markLocalFileDeleted(chunkId: String)

    @Query("SELECT driveFolderId FROM chunk_uploads WHERE projectName = :projectName AND driveFolderId IS NOT NULL LIMIT 1")
    suspend fun getCachedFolderId(projectName: String): String?

    @Query("UPDATE chunk_uploads SET driveFolderId = :folderId WHERE chunkId = :chunkId")
    suspend fun setFolderId(chunkId: String, folderId: String)

    @Query("DELETE FROM chunk_uploads")
    suspend fun deleteAll()

    @Query("SELECT chunkId FROM chunk_uploads WHERE status = 'FAILED'")
    suspend fun getFailedChunkIds(): List<String>
}
