package com.cloudrecorder.phase2.upload

import android.accounts.Account
import android.content.Context
import android.content.Intent
import com.google.android.gms.auth.GoogleAuthException
import com.google.android.gms.auth.GoogleAuthUtil
import com.google.android.gms.auth.UserRecoverableAuthException
import com.google.android.gms.auth.api.signin.GoogleSignIn
import com.google.android.gms.auth.api.signin.GoogleSignInClient
import com.google.android.gms.auth.api.signin.GoogleSignInOptions
import com.google.android.gms.common.api.Scope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.IOException

/**
 * Google Sign-In with the drive.file scope (same minimal scope as the Phase 1 desktop
 * app — this app can only see/manage files and folders it creates itself). Unlike
 * Phase 1's manual token.json, token storage/refresh here is handled entirely by
 * Play Services' on-device AccountManager: once signed in, getAccessToken() below
 * silently returns a valid (refreshed as needed) token from any thread, including a
 * background WorkManager worker with no UI on screen — no separate token file to
 * manage ourselves.
 */
object DriveAuthManager {
    const val SCOPE_DRIVE_FILE = "https://www.googleapis.com/auth/drive.file"

    class NotSignedInException : Exception("Not signed in to Google")
    class RecoverableAuthException(val intent: Intent?) : Exception("Re-authentication required")

    fun signInClient(context: Context): GoogleSignInClient {
        val options = GoogleSignInOptions.Builder(GoogleSignInOptions.DEFAULT_SIGN_IN)
            .requestScopes(Scope(SCOPE_DRIVE_FILE))
            // DEFAULT_SIGN_IN alone does not request the email scope, so without
            // this GoogleSignInAccount.email is always null — sign-in succeeds
            // (drive.file is granted, currentAccount() works fine) but currentEmail()
            // silently returns null forever, so the UI never shows as signed in even
            // though it genuinely is. Confirmed via the actual cached account: scopes
            // were ["drive.file", "openid", "profile"] with no email/email-scope.
            .requestEmail()
            .build()
        return GoogleSignIn.getClient(context, options)
    }

    fun currentAccount(context: Context): Account? =
        GoogleSignIn.getLastSignedInAccount(context)?.account

    fun currentEmail(context: Context): String? =
        GoogleSignIn.getLastSignedInAccount(context)?.email

    /**
     * Blocking token fetch (must be called off the main thread). Throws
     * NotSignedInException if nobody's signed in, or RecoverableAuthException if Play
     * Services needs interactive consent again (e.g. the grant was revoked) — the
     * caller should catch that, and if it's on a Worker (no UI available) just fail
     * the attempt and surface a "needs re-authentication" status instead of launching
     * the intent itself.
     */
    suspend fun getAccessToken(context: Context): String = withContext(Dispatchers.IO) {
        val account = currentAccount(context) ?: throw NotSignedInException()
        try {
            GoogleAuthUtil.getToken(context, account, "oauth2:$SCOPE_DRIVE_FILE")
        } catch (e: UserRecoverableAuthException) {
            throw RecoverableAuthException(e.intent)
        } catch (e: GoogleAuthException) {
            throw IOException("Google auth failed: ${e.message}", e)
        }
    }
}
