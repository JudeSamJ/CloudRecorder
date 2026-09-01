package com.cloudrecorder.phase2.upload

import androidx.room.TypeConverter

class Converters {
    @TypeConverter
    fun fromStatus(status: UploadStatus): String = status.name

    @TypeConverter
    fun toStatus(value: String): UploadStatus = UploadStatus.valueOf(value)
}
