package com.cloudrecorder.phase2.upload

import android.content.Context
import androidx.room.Database
import androidx.room.Room
import androidx.room.RoomDatabase
import androidx.room.TypeConverters

@Database(
    entities = [ChunkUploadEntity::class, SessionEntity::class],
    version = 2,
    exportSchema = false,
)
@TypeConverters(Converters::class)
abstract class UploadDatabase : RoomDatabase() {
    abstract fun chunkUploadDao(): ChunkUploadDao
    abstract fun sessionDao(): SessionDao

    companion object {
        @Volatile
        private var instance: UploadDatabase? = null

        fun get(context: Context): UploadDatabase = instance ?: synchronized(this) {
            instance ?: Room.databaseBuilder(
                context.applicationContext,
                UploadDatabase::class.java,
                "upload_queue.db",
            )
                // Phase 6 added the `sessions` table (schema v2). Destructive rather
                // than a hand-written migration: this is still active cross-phase
                // development, not production data, and a wrong hand-written
                // migration SQL would crash on open instead of failing safely. Cost:
                // any chunk still PENDING/FAILED in the queue at update time is
                // dropped from the DB (the local video file itself is untouched) and
                // needs a fresh recording or manual re-trigger to re-upload.
                .fallbackToDestructiveMigration()
                .build()
                .also { instance = it }
        }
    }
}
