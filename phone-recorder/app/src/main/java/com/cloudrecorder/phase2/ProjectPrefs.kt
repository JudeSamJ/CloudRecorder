package com.cloudrecorder.phase2

import android.content.Context

/** Remembers the last-used project name across app restarts (simple, no DB needed). */
object ProjectPrefs {
    private const val PREFS_NAME = "cloud_recorder_prefs"
    private const val KEY_PROJECT_NAME = "project_name"

    fun load(context: Context): String =
        context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE).getString(KEY_PROJECT_NAME, "") ?: ""

    fun save(context: Context, projectName: String) {
        context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
            .edit()
            .putString(KEY_PROJECT_NAME, projectName)
            .apply()
    }
}
