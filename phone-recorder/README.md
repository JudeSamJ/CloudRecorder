# Cloud Content Pipeline — Phase 2: Phone Recording (standalone)

A minimal Android (Kotlin, CameraX) app that records video in short chunks and logs
its own reliability data — no upload, no Drive integration, no FFmpeg. That's all
later phases. This phase exists to answer one question empirically: **does this
phone survive a long screen-locked background recording without the OS killing or
throttling it?**

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

## What this phase does not do (by design)

No upload, no Google Drive integration, no network code, no proxy generation, no
FFmpeg, no unlimited-storage-growth handling beyond a low-storage warning (500MB)
and an automatic stop at critically low storage (50MB). Chunks just accumulate in
app-private local storage (`Android/data/com.cloudrecorder.phase2/files/Movies/sessions/`)
until you clear them from the app or pull them off via `adb`/file manager.
