# Cloud Content Pipeline — Phone App (Phases 2 + 3)

An Android (Kotlin, CameraX) app that records video in short chunks (Phase 2) and
uploads each one to Google Drive via a resumable, restart-proof queue (Phase 3). No
proxy generation, no FFmpeg, no chunk reassembly — those are later phases.

## Design decisions (confirmed before implementation)

**Chunking format — sequential finalized MP4 files, not fragmented MP4 (fMP4).**
Each chunk (`chunk_0001.mp4`, `chunk_0002.mp4`, ...) is a complete, independently
playable file, written via CameraX's `Recorder` stop → finalize → start cycle. A true
fMP4 continuous stream would need a hand-rolled `MediaCodec`/`MediaMuxer` pipeline —
real complexity that belongs in a later phase if this phase's data shows chunk
boundaries are a problem. For now, plain MP4 chunks are easy to reassemble later
(concat) and let the app directly measure and log the thing you actually want to
know: how clean or lossy are the chunk-boundary restarts, and does recording survive
at all in the background. Expect a small (usually well under 300ms) gap at each
chunk boundary; the app logs a warning whenever a restart gap exceeds 300ms.

**Foreground service, not a plain background service.** Camera/mic access from the
background requires a foreground service with a persistent notification. On Android
14+ (API 34) the service also declares `foregroundServiceType="camera|microphone"`
in the manifest and starts with those types explicitly (`ServiceCompat.startForeground`).
The service is started from a direct user tap on "Start Recording" in the app's own
UI — Android 12+ blocks starting a foreground service from a background/passive
trigger, so there's no boot-time or scheduled auto-start in this phase.

**`START_NOT_STICKY` is intentional.** If Android kills the service, we want that to
be a visible, logged fact — not silently masked by an automatic restart. The service
logs an `ERROR`-level entry in `onDestroy()` if it's torn down while still recording
(i.e., not from the user's own Stop button), and a `WARN` entry in `onTaskRemoved()`
if you swipe the app away from Recents while recording, so you can see in the log
whether either of those killed the recording or not.

## Background execution constraints you're testing against

- **Doze mode / App Standby normally do *not* kill an active foreground service** —
  the persistent notification is Android's built-in exemption. The bigger native
  Android risks are thermal/CPU throttling on long 4K recordings and, more rarely,
  the camera being reclaimed by the system.
- **OEM battery managers are the real risk**, and they often ignore the standard
  foreground-service exemption:
  - **Xiaomi (MIUI)** — most aggressive; needs "Autostart" manually enabled and can
    still force-stop regardless.
  - **Samsung (One UI)** — "Put unused apps to sleep" / battery optimization list;
    add this app to "Never sleeping apps" / unrestricted.
  - **Huawei/Honor, OnePlus, Oppo/Realme, Vivo** — all have their own
    autostart/protected-apps manager on top of stock Doze.
  - **Stock Android / Pixel** — generally the most reliable baseline.

  Before testing: go to **Settings → Battery → Battery optimization** for this app
  and set it to unrestricted/"don't optimize", and check for any OEM-specific
  autostart/protected-app list.

## Build and install

### Option A: Android Studio (recommended)

1. Open Android Studio → **Open** → select the `phone-recorder/` folder.
2. Let Gradle sync (Android Studio will fetch the Gradle wrapper jar automatically
   the first time — `gradle-wrapper.jar` itself isn't committed to source control).
3. Connect your phone via USB with **USB debugging** enabled (Settings → About
   phone → tap Build number 7 times → Developer options → USB debugging).
4. Click **Run ▶** with your phone selected as the target device.

### Option B: command line

```
cd phone-recorder
./gradlew installDebug
```

(First run downloads the Gradle distribution — needs internet access once.)

## Verifying it works

1. Launch the app, grant Camera/Microphone/Notifications permissions when prompted.
2. Confirm the **Quality** chips show your device's actually-detected options (not
   just a hardcoded 1080p/4K) — this list comes from `QualitySelector.getSupportedQualities()`
   on your specific camera.
3. Pick a quality and chunk length, tap **Start Recording**. Confirm the persistent
   notification appears and stats (duration, chunk count) start updating.
4. Tap **Stop Recording** after a short test; confirm the summary card shows chunk
   count / size / duration, and check `Android/data/com.cloudrecorder.phase2/files/Movies/sessions/<timestamp>/`
   on the device (via a file manager with that access, or `adb pull`) for the chunk
   files and `event_log.txt`.

## The real test: 20–30 minute screen-locked background recording

1. Set battery optimization for this app to unrestricted (see above) and check any
   OEM autostart list.
2. Tap **Start Recording**, then lock the screen (don't swipe the app away yet) and
   leave the phone alone for 20–30 minutes.
3. Unlock and check:
   - Is the notification still showing an increasing duration/chunk count?
   - Open the app — does the Event Log show a steady stream of "Chunk N started" /
     "Chunk N finalized" entries with no long gap, and no `ERROR`/`onDestroy`
     entry partway through?
   - `adb pull` the session folder and confirm you have roughly
     `(test duration ÷ chunk length)` chunk files, each with sane file sizes for the
     chosen quality (not zero-byte).
4. Repeat once more, this time swiping the app away from Recents shortly after
   starting (screen still on or off) — check whether `onTaskRemoved` logs but
   recording continues (expected, since the foreground service is independent of
   the task), or whether the OS actually tears the service down (`onDestroy` error
   log) shortly after.

**What "reliable" looks like:** the log shows continuous chunk start/finalize pairs
for the whole duration, gaps consistently under a few hundred ms, no `ERROR`-level
onDestroy entry, and the pulled file count/sizes match expectations.

**What "this won't work reliably" looks like:** the log (and/or notification) stops
updating partway through with no Stop action from you, `adb pull` afterward shows an
`ERROR`-level "onDestroy() called while still recording" entry mid-session, or the
chunk count on-device is noticeably lower than `elapsed time ÷ chunk length`. If you
see that, the next step is checking your phone's specific OEM battery-manager
settings before concluding background recording isn't viable on this device — this
is exactly the ambiguity flagged above that I can't verify without your specific
hardware.

## What Phase 2 does not do (by design)

No upload, no Google Drive integration, no network code beyond what Phase 3 adds
below, no proxy generation, no FFmpeg, no unlimited-storage-growth handling beyond a
low-storage warning (500MB) and an automatic stop at critically low storage (50MB).

---

# Phase 3: chunked, resumable upload to Google Drive

Each finalized chunk is queued for upload to `Content Creation/Projects/<project>/Original/`
in your Drive (same layout as the Phase 1 desktop app), using the Drive v3 **resumable**
upload protocol, and is only deleted locally after Drive confirms it. The queue survives
app kills, crashes, and reboots.

## Design decisions

**Local queue: Room (SQLite), not flat files.** One row per chunk (status, resumable
session URI + its age, retry count, Drive folder id, etc.). This needs atomic,
crash-safe updates (mark-uploaded-then-delete-file must never leave things
inconsistent if the process dies mid-write) and ordered/filtered queries — both
trivial with SQLite, both awkward to get right by hand with JSON files.

**Upload engine: WorkManager**, not a hand-rolled retry loop. Each chunk gets a
`CoroutineWorker` (`ChunkUploadWorker`) enqueued with a `NetworkType.CONNECTED`
constraint and a unique work name equal to the chunk's id (`ExistingWorkPolicy.KEEP`)
— that uniqueness is what guarantees a chunk is never enqueued, and therefore
uploaded, twice. This gets you, from a well-tested library:
- **Offline pausing** — the OS won't run the worker at all without connectivity.
- **Resume after reboot** — WorkManager persists its own queue and reschedules itself
  after reboot via a receiver bundled in the library, without needing the app to be
  reopened first, once the network constraint is satisfiable.
- **Exponential backoff** between attempts.

A separate `retryCount` column distinguishes "still retrying" from a terminal
"failed, needs your attention" state — after 8 attempts a chunk is marked `FAILED`
and surfaced with a manual **Retry** button, rather than retrying forever silently.

**Resumable session URIs: expiry and staleness handling.** Google's resumable
session URIs are valid roughly **1 week** from creation. Every URI is stored with its
creation timestamp; before reuse, if it's older than **6 days** (safety margin) it's
discarded and a fresh session is started for that chunk. Even within that window,
every resume first sends Drive a zero-byte status-check PUT
(`Content-Range: bytes */<size>`) rather than trusting locally-cached progress, since
local state could be stale if the process died mid-write. A `404`/`410` response
there also means "session's gone" — same fallback: restart that one chunk's upload
from byte 0. This is the only case where a chunk's bytes get re-sent; it doesn't
create duplicates or corrupt ordering, it just costs re-uploading that one chunk.

**OAuth on Android is separate from Phase 1's setup.** Scope is still `drive.file`.
You need a **second OAuth client** in the same Google Cloud project, of type
**Android**, registered with this app's package name and your debug keystore's SHA-1
fingerprint (see setup below). Token storage/refresh is handled by Play Services'
on-device account manager — no manual token file.

**Recording is never blocked by auth or network** (per your call): Start Recording
works regardless of sign-in or connectivity state; chunks simply queue locally with a
clear banner until you're signed in and/or back online.

**Project targeting is a manual text field** matching a Phase 1 project name — the
phone app finds-or-creates that project's folder structure via the Drive API so both
apps agree on layout.

## Additional Android OAuth client setup (on top of Phase 1's Google Cloud project)

1. Get your debug keystore's SHA-1 fingerprint:
   ```
   cd phone-recorder
   ./gradlew signingReport
   ```
   Look for the `SHA1` line under the `debug` variant.
2. In the same Google Cloud project as Phase 1, go to **APIs & Services →
   Credentials → Create Credentials → OAuth client ID**.
3. Application type: **Android**. Package name: `com.cloudrecorder.phase2`. Paste the
   SHA-1 from step 1.
4. No `google-services.json` or client secret needed on the Android side — Play
   Services resolves the right client from your app's package name + signing
   certificate automatically at sign-in time.
5. On the OAuth consent screen (same one as Phase 1), make sure your account is
   still listed as a test user.

## Using it

1. Launch the app, tap **Sign in with Google**, and grant Drive access (`drive.file`
   scope only).
2. Enter a **Project name** matching (or that you want to create in) your Drive —
   e.g. `YouTube_003`. This is remembered across app restarts.
3. Start recording as usual. The **Upload status** card shows chunks recorded /
   uploaded / uploading / pending / failed, and the current local buffer size.
4. If you're offline or not signed in, the banner at the top says so explicitly —
   chunks keep recording and buffering locally either way; the buffer only stays
   small while uploads are actually flowing.
5. If any chunk shows as failed after retries, a **Retry failed chunk(s)** button
   appears on the Upload status card.

Each uploaded file is named `<sessionId>_chunk_NNNN.mp4` and tagged with Drive
`appProperties` (`sessionId`, `chunkIndex`, `recordedAtMs`) so Phase 4 can later
verify a session's chunks are all present and correctly ordered.

**Phase 6 addition:** once a *stopped* session's chunks are all confirmed
`UPLOADED`, the app also uploads a small `<sessionId>_complete.json` marker
(tagged `appProperties: {sessionId, kind: session_complete, projectName,
chunkCount, totalBytes}`) to the same `Original/` folder. This is the
deterministic "this session is genuinely done" signal the Phase 6 desktop
companion watches for — see the root `README.md`'s Phase 6 section. It's written
by a separate `SessionMarkerWorker`, only enqueued once, and never written for a
session that was still recording when the app or its foreground service was
killed (an OS-kill can't know the true final chunk count).

## Test protocol: simulating real-world network flakiness

For all three, start a recording with a short chunk length (5s) so you get several
chunks per minute of testing, and keep the Event Log and Upload status card visible
(reopen the app periodically to check).

### 1. Airplane mode mid-recording

1. Start recording, confirm a few chunks upload successfully (status card shows
   increasing "Uploaded").
2. Enable Airplane Mode. Confirm the offline banner appears and "Pending" starts
   climbing while "Uploaded" stops.
3. Keep recording for a few minutes under airplane mode.
4. Disable Airplane Mode. **Check:** uploads resume automatically within moments
   (WorkManager's network constraint firing), "Pending" drains back down, no chunk
   ever shows as duplicated in Drive (check the Original/ folder — file count should
   equal total chunks recorded, not more).

### 2. Force-killing the app mid-upload

1. Start recording on WiFi or mobile data, let a large chunk (use 10s chunk length,
   4K if available, for a bigger file) start uploading.
2. While the Upload status card shows "Uploading: 1", force-stop the app from
   Android Settings → Apps → CloudRecorder Phase 2 → Force stop. (This also kills the
   foreground recording service — expect an `ERROR` "onDestroy while recording"
   entry the next time you check the log, and recording itself will have stopped;
   this test is specifically about the upload queue, not recording continuity.)
3. Reopen the app. **Check:** the interrupted chunk's status recovers to
   `Pending`/`Uploading` again (via `recoverPendingUploads()` on launch) without you
   doing anything, and it eventually completes. In Drive, confirm that chunk appears
   exactly once (the resumable session resumes from whatever byte offset Drive
   already had — check the Event Log for a "resumable session" log line — it does
   not restart from zero unless the session had actually expired).

### 3. Switching networks mid-upload (WiFi ↔ mobile data)

1. Start recording on WiFi, let uploads begin.
2. Mid-upload, turn off WiFi so the phone switches to mobile data (or vice versa).
3. **Check:** the in-flight upload either completes via the new network path or fails
   once and is picked up on retry (Event Log shows a `WARN` retry line) — either way
   it should complete without duplicating in Drive. Confirm chunk count in Drive
   still matches chunks recorded once the test session ends.

### What "reliable" looks like

Across all three tests: the number of files in Drive's `Original/` folder for that
session equals the number of chunks recorded (check the Upload status card's
"Recorded" count against Drive's file count) — never more (no duplicates), never
fewer once you've waited for pending/failed items to clear. Chunk `appProperties` in
Drive show a clean, gapless `chunkIndex` sequence per `sessionId`.

### What "this needs more work" looks like

A chunk stuck in "Failed" that the Retry button doesn't clear (check the Event Log's
`ERROR` line for that chunk — likely a genuine auth or quota issue, not a queue bug),
a chunk count in Drive that doesn't match what was recorded after everything's had
time to settle, or the same chunk appearing twice in Drive (would indicate a unique
work name or resumable-offset bug — worth flagging back for investigation, since the
whole design is built around preventing exactly that).

## What Phase 3 does not do (by design)

No chunk reassembly or master-video reconstruction (Phase 4), no proxy generation, no
FFmpeg. This phase's job ends once chunks are verifiably sitting in Drive.
