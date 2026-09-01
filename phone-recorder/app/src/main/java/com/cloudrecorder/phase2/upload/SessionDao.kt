package com.cloudrecorder.phase2.upload

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.Query

@Dao
interface SessionDao {

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun upsert(entity: SessionEntity)

    @Query("SELECT * FROM sessions WHERE sessionId = :sessionId")
    suspend fun getById(sessionId: String): SessionEntity?

    @Query("SELECT * FROM sessions WHERE markerUploaded = 0")
    suspend fun getAllMarkerPending(): List<SessionEntity>

    @Query("UPDATE sessions SET markerUploaded = 1, markerDriveFileId = :driveFileId WHERE sessionId = :sessionId")
    suspend fun markMarkerUploaded(sessionId: String, driveFileId: String)

    @Query("SELECT COUNT(*) FROM chunk_uploads WHERE sessionId = :sessionId AND status = 'UPLOADED'")
    suspend fun countUploadedChunks(sessionId: String): Int
}
