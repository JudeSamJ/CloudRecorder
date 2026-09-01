package com.cloudrecorder.phase2.upload

import android.content.Context
import androidx.room.Database
import androidx.room.Room
import androidx.room.RoomDatabase
import androidx.room.TypeConverters

@Database(entities = [ChunkUploadEntity::class], version = 1, exportSchema = false)
@TypeConverters(Converters::class)
abstract class UploadDatabase : RoomDatabase() {
    abstract fun chunkUploadDao(): ChunkUploadDao

    companion object {
        @Volatile
        private var instance: UploadDatabase? = null

        fun get(context: Context): UploadDatabase = instance ?: synchronized(this) {
            instance ?: Room.databaseBuilder(
                context.applicationContext,
                UploadDatabase::class.java,
                "upload_queue.db",
            ).build().also { instance = it }
        }
    }
}
