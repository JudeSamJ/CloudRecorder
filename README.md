# Cloud Content Pipeline — Desktop App (Phases 1, 4 + 5)

A local CLI that authenticates with your personal Google account, manages a
project folder structure in your Google Drive, reassembles a recording
session's uploaded chunks into one validated master video (Phase 4), and
generates a DaVinci Resolve editing proxy from that master (Phase 5):

```
Content Creation/
  Projects/
    <ProjectName>/
      Original/
      Proxy/
      Audio/
      Resolve/
  Archive/
```

DaVinci Resolve project automation itself is a later phase and is not
implemented here.

## 1. Install dependencies

```
pip install -r requirements.txt
```

## 2. Create a Google Cloud project and enable the Drive API

1. Go to the [Google Cloud Console](https://console.cloud.google.com/).
2. Create a new project (or pick an existing one) — top-left project
   selector → "New Project".
3. With that project selected, go to **APIs & Services → Library**, search
   for **Google Drive API**, and click **Enable**.

## 3. Configure the OAuth consent screen

1. Go to **APIs & Services → OAuth consent screen**.
2. Choose **External** (unless you have a Google Workspace org) and fill in
   the required fields (app name, your email as support/developer contact).
3. On the **Scopes** step you don't need to add anything manually — the app
   requests `drive.file` at runtime.
4. On the **Test users** step, add your own Google account email. While the
   app is in "Testing" mode, only test users can authenticate — that's fine
   for a personal tool.

## 4. Create OAuth client credentials

1. Go to **APIs & Services → Credentials → Create Credentials → OAuth
   client ID**.
2. Application type: **Desktop app**.
3. Give it any name (e.g. "Cloud Content Pipeline CLI").
4. Click **Create**, then **Download JSON**.
5. Rename the downloaded file to `credentials.json` and place it in the
   root of this project (next to `drive_manager.py`).

`credentials.json` is gitignored — it identifies the OAuth client, not you
personally, but keep it private regardless.

## 5. First run / authentication

Run any command, e.g.:

```
python drive_manager.py list-projects
```

The first time, this opens your default browser to Google's consent
screen. Sign in with the same Google account as your Drive/Google One
storage, and approve access. The app only requests the `drive.file`
scope — it can only see and manage files/folders it creates itself, not
your entire Drive.

After you approve, a `token.json` file is created in the project root.
This stores your access/refresh token so you won't need to re-authenticate
on future runs — the app refreshes the access token automatically using
the refresh token. `token.json` is gitignored and never committed.

If your authorization is later revoked (e.g. you remove access in your
Google Account settings) or `token.json` becomes invalid, the CLI will
print a clear error telling you to delete `token.json` and re-run to
re-authenticate.

## 6. Usage

Create a new project's folder set:

```
python drive_manager.py create-project "YouTube_003"
```

This ensures `Content Creation/Projects/` and `Content Creation/Archive/`
exist (creating them if needed), then creates
`Content Creation/Projects/YouTube_003/` with `Original/`, `Proxy/`,
`Audio/`, and `Resolve/` subfolders. It prints the new folder's Drive link.

List existing projects:

```
python drive_manager.py list-projects
```

## 7. Verifying it worked

1. Run `python drive_manager.py create-project "Test_001"`.
2. Open the printed `https://drive.google.com/drive/folders/...` link, or
   just go to [drive.google.com](https://drive.google.com/) and look for
   **Content Creation → Projects → Test_001**, with `Original`, `Proxy`,
   `Audio`, `Resolve` subfolders inside.
3. Run `python drive_manager.py list-projects` and confirm `Test_001` is
   listed.
4. Run `python drive_manager.py create-project "Test_001"` again — it
   should fail with a clear "already exists" error instead of creating a
   duplicate.

## Error handling

The CLI catches and reports, without a raw stack trace:

- No internet connection / Drive API unreachable
- Expired or revoked OAuth token (tells you to delete `token.json`)
- Duplicate project names
- Drive API rate limit / quota errors (retries with exponential backoff
  first, then reports clearly if it still fails)

## Files

- `drive_manager.py` — CLI entry point
- `pipeline/auth.py` — OAuth2 flow, token load/refresh
- `pipeline/drive_client.py` — Drive API v3 wrapper, retry/backoff, error translation
- `pipeline/project_manager.py` — folder-tree logic
- `pipeline/reconstruction.py` — Phase 4: session verification + master reconstruction
- `pipeline/ffmpeg_tools.py` — Phase 4 & 5: ffmpeg/ffprobe subprocess wrappers
- `pipeline/proxy_generation.py` — Phase 5: DNxHR proxy generation
- `pipeline/drive_desktop.py` — Phase 5: local Drive-sync path discovery
- `pipeline/errors.py` — custom exceptions
- `credentials.json`, `token.json` — local secrets, gitignored, not included in this repo

---

# Phase 4: session verification + master video reconstruction

Once a phone recording session's chunks are sitting in `Original/` (Phase 3),
`reconstruct` verifies the sequence is complete, stitches the chunks into one
master video, validates it, and only then uploads the master and deletes the
source chunks from Drive.

```
python drive_manager.py reconstruct <session-id>
```

The session ID is the same one tagged on each chunk's Drive `appProperties`
by the phone app (visible in the phone app's Event Log, or as the
`chunk_XXXX.mp4` files' shared `<sessionId>_chunk_NNNN.mp4` prefix in Drive).
You don't need to specify which project — chunks are found globally by their
`sessionId` tag, and the project folder is inferred from where they live.

## Requirements

- **FFmpeg and ffprobe must be installed and on your PATH.** The CLI checks
  for both up front and fails with an install pointer
  (https://ffmpeg.org/download.html) if either is missing, before touching
  Drive or local disk.

## Design decisions

**Concat method: the ffmpeg concat *demuxer* with stream copy**
(`-f concat -safe 0 -i list.txt -c copy`), not the concat protocol or concat
filter. All chunks in a session come from the same CameraX `Recorder` run
with identical codec settings — the concat demuxer is built exactly for
stitching same-codec segments at the container level, copying packets
without decoding, which is lossless, fast, and can't introduce the
audio/video desync that re-encoding (the concat *filter*) risks. The concat
*protocol* isn't usable at all here — it only supports a handful of formats
like MPEG-TS, not MP4.

**Local temp disk usage: roughly 2× the final master's size, briefly.**
Chunks are downloaded (~1× final size, since stream-copy concatenation
doesn't change total bytes) and the assembled master is written alongside
them (another ~1×) before upload. The temp directory is created with
`tempfile.mkdtemp()` and removed in a `finally` block — cleaned up whether
reconstruction succeeds, fails validation, or crashes partway through.

**Order of operations is fixed and cannot be optimized away:** verify
completeness → download → concat → validate (duration + decode integrity) →
upload master → *only then* delete chunks. If validation fails, the process
stops there — no upload, no deletion; the chunks remain in Drive as the
source of truth and the failure is written to a Drive-side report so you
know exactly what happened without needing to have watched the terminal.

**Session lookup is global, not folder-scoped.** Chunks are found by
querying Drive for `appProperties has { key='sessionId' and value='<id>' }`
directly (the `drive.file` scope only shows files this app created anyway),
so a session that's sat around unreconstructed for a while is found the same
way regardless of which project it belongs to — you don't need to remember
or re-specify the project name.

**Both missing chunks and duplicate chunk-index values are treated as fatal**,
reported by exact index (e.g. "missing chunk index(es) [5, 6]"), not just a
generic "incomplete" message — this is checked before any download or ffmpeg
work happens.

**Idempotency guard:** if `<sessionId>_master.mp4` already exists in the
project's `Original/` folder, reconstruction refuses outright rather than
silently overwriting or duplicating it. Delete the existing master first if
you intentionally want to redo reconstruction.

**Reconstruction reports are uploaded to Drive**, saved as
`<sessionId>_reconstruction_report_<timestamp>.txt` in the project's
`Original/` folder — both for a successful run (chunks found, durations,
validation results, upload/deletion confirmation) and for an aborted one
(exactly which chunk indices were missing/duplicated). Each attempt gets its
own timestamped report rather than overwriting a prior one, so a session you
retried after fixing a gap keeps its failure history alongside the eventual
success.

## Usage and output

```
$ python drive_manager.py reconstruct 20260901_143022
Looking up chunks for session '20260901_143022'...
Downloading chunk 1/42: 20260901_143022_chunk_0001.mp4
...
Probing chunk durations...
Concatenating 42 chunks with ffmpeg (stream copy)...
Validating reconstructed master...
Uploading master '20260901_143022_master.mp4' to Drive...
  upload progress: 23%
  ...
Deleting 42 chunk files from Drive...
Done.

Reconstruction succeeded.
  Master file: 20260901_143022_master.mp4
  Duration: 341.20s
  Size: 812.4 MB
  Chunks reassembled: 42
  Drive file id: 1AbCdEfGhIjKlMnOpQrStUvWxYz
  Source chunks have been deleted from Drive.
```

On a missing-chunk failure:

```
$ python drive_manager.py reconstruct 20260901_143022
Looking up chunks for session '20260901_143022'...
Error: Session incomplete/corrupt: missing chunk index(es) [17]
  Missing chunk index(es): [17]
  Duplicate chunk index(es): []
A reconstruction report with these details was uploaded to the project's Original/ folder.
```

## Test protocol

### 1. Intentionally testing a missing chunk

1. Record and let a short session (e.g. 6-8 chunks) fully upload via the
   phone app.
2. In Drive, manually delete one chunk file from the middle of the sequence
   (e.g. chunk 4 of 8) — this simulates a lost/failed upload.
3. Run `python drive_manager.py reconstruct <session-id>`.
4. **Expected:** the command fails immediately after the chunk lookup step
   (no download, no ffmpeg run), reports "missing chunk index(es) [4]"
   precisely, exits non-zero, and a reconstruction report documenting the
   gap appears in the project's `Original/` folder. Confirm no master file
   was created and no remaining chunks were deleted — the folder should be
   unchanged except for the new report.
5. Re-upload/replace the missing chunk (or re-record), then re-run
   `reconstruct` and confirm it now succeeds.

### 2. Verifying a reconstructed master is frame-accurate, not just "a file exists"

Duration-matching and the ffmpeg decode pass catch corruption and gross
mismatches, but to independently confirm frame accuracy against the original
chunks yourself:

1. Before running `reconstruct`, download the session's chunks yourself
   (or note their Drive file IDs) so you still have a copy to compare
   against even after Phase 4 deletes them from Drive.
2. After reconstruction, download the resulting master file.
3. Compare frame counts: run
   `ffprobe -v error -count_frames -select_streams v:0 -show_entries stream=nb_read_frames -of default=nokey=1:noprint_wrappers=1 <file>`
   on each original chunk and sum the results, then run the same command on
   the master file. The totals should match exactly (stream-copy
   concatenation doesn't add, drop, or duplicate frames).
4. Spot-check sync at a chunk boundary: seek the master to a timestamp near
   a boundary you know from the original chunk durations (e.g. the end of
   chunk 3 into the start of chunk 4) and visually confirm audio and video
   still line up — `ffplay -ss <seconds> <master file>` is enough for a
   spot check, no need to watch the whole thing.
5. Compare total duration precisely: `ffprobe -v error -show_entries
   format=duration -of default=nokey=1:noprint_wrappers=1` on the master
   should be within roughly a frame or two's worth of time of the sum of the
   original chunks' durations — Phase 4's own validation checks this with a
   deliberately loose tolerance (2 seconds or 2%, whichever is larger) to
   avoid false-positive failures on trivial container-timestamp rounding;
   your manual check can be tighter since you're doing it once, by hand,
   specifically to build trust in the automated check.

If frame counts match and the boundary spot-check sounds continuous, you can
trust the automated validation for future sessions without repeating this
manual check every time.

---

# Phase 5: automatic proxy generation

Once a session has a reconstructed master (Phase 4), `generate-proxy` builds
a DaVinci Resolve editing proxy from it, validates it the same way Phase 4
validates the master, and uploads it to the project's `Proxy/` folder.

```
python drive_manager.py generate-proxy <session-id>
python drive_manager.py generate-proxy --watch [--interval SECONDS]
```

`--watch` polls Drive (default every 60s) for any reconstructed master that
doesn't have a proxy yet and generates one automatically — opt-in, not the
only way to trigger this; you can always run it for one session manually
instead, e.g. if you've reconstructed several sessions but only want to
proxy the one you're about to edit.

## Design decisions

**Codec/resolution: DNxHR LB, half-resolution, PCM audio, .mov container.**
DNxHR is still the right call for a Resolve+Windows editing proxy — it's
Avid's openly-specified, cross-platform, intra-frame-only codec built for
exactly this (unlike H.264/HEVC, which are inter-frame delivery codecs that
scrub badly in an editor no matter how fast they encode). The **LB**
("Low Bandwidth") profile is specifically meant for half/quarter-res proxy
work, which is why it's the default rather than SQ. Half-resolution
(rounded down to even dimensions, since codecs generally require that) with
PCM audio in a `.mov` container matches the standard convention for
ffmpeg-generated Resolve proxies.

**Hardware acceleration is real but partial — flagged honestly, not faked.**
NVENC and Quick Sync only expose hardware *encoders* for H.264/HEVC/AV1;
there's no hardware DNxHR encoder in ffmpeg. So hardware acceleration here
means hardware-accelerated *decoding* of the source master (`-hwaccel
cuda`/`qsv`) while the DNxHR encode itself always runs in software — that's
genuinely faster than fully-software decode+encode when a GPU is available
(decode is often the more expensive part for a 4K H.264/HEVC master), but
it's not the full hardware speedup "NVENC" might suggest. ffmpeg is queried
once for which hwaccel it was built with support for; if the actual encode
attempt with that flag fails (e.g. no matching GPU present despite ffmpeg
supporting the flag), it's retried once with plain software decode rather
than failing the job — verified in testing by forcing a bogus hwaccel value
and confirming the fallback still produces a valid proxy.

**Filename convention for Resolve's auto-relink: identical filename stem,
extension only differs.** A proxy named `<sessionId>_master.mp4` becomes
`<sessionId>_master.mov` — same stem, so Resolve's own matching (which
compares by filename stem, ignoring extension) finds it automatically. This
is what makes the "no manual relinking" goal achievable: point Resolve's
**Project Settings → Master Project Settings → General Options → Proxy
media path** at this project's local (Drive-synced) `Proxy/` folder once,
and every same-stemmed file placed there is picked up without a per-clip
"Link Proxy Media" pass.

**Local Drive-for-desktop path: detected, never assumed.** The proxy is
uploaded via the Drive API — Drive for desktop syncs it down independently
of anything this app does, regardless of how it's configured. Local-path
detection exists only to tell you where to point Resolve and to optionally
confirm the sync landed:
- **Streaming mode** (Drive's default): detected by finding which Windows
  drive letter has the volume label "Google Drive" — the letter itself is
  user-configurable, so this doesn't assume a fixed one like `G:`.
- **Mirror mode** (synced to an ordinary folder): a few common default
  locations are checked (`~/Google Drive/My Drive`, `~/My Drive`, etc.).
- An environment variable, `CLOUDRECORDER_DRIVE_LOCAL_PATH`, overrides both
  if auto-detection ever guesses wrong or you have a nonstandard setup.
- If nothing is found, the CLI says so plainly and tells you to check Drive
  for desktop's own settings — this never blocks or fails the run, since the
  proxy is already safely uploaded by that point regardless.

**Validated before upload, same standard as Phase 4:** duration-sum
tolerance (2 seconds or 2%, whichever is larger) against the master, plus a
full ffmpeg decode pass. A validation failure means no upload happens at
all — you get a clear error instead of a proxy that looks done but isn't.

**Idempotency guard**, consistent with Phase 4: if a proxy already exists
for a session (tracked via Drive `appProperties`, not filename guessing),
generation refuses rather than creating a duplicate — delete the existing
proxy first to regenerate it.

## Usage and output

```
$ python drive_manager.py generate-proxy 20260901_143022
Looking up reconstructed master for session '20260901_143022'...
Downloading master '20260901_143022_master.mp4'...
Encoding proxy (DNxHR LB, half-resolution, PCM audio, .mov container) at 1920x1080, trying cuda-accelerated decode...
  encoding: 34%, ETA 41s
  encoding: 100%
Validating proxy...
Uploading proxy '20260901_143022_master.mov' to Drive...
  upload progress: 100%
Waiting for local sync at G:\My Drive\Content Creation\Projects\YouTube_003\Proxy\20260901_143022_master.mov...

Proxy generation succeeded.
  Proxy file: 20260901_143022_master.mov
  Duration: 341.18s
  Resolution: 1920x1080
  Size: 210.4 MB
  Hardware-accelerated decode: cuda
  Local path: G:\My Drive\Content Creation\Projects\YouTube_003\Proxy\20260901_143022_master.mov (confirmed synced)
```

## Test protocol

### 1. Generate a proxy and confirm Resolve auto-relinks it

1. Run `reconstruct` for a real session (Phase 4), then
   `generate-proxy <session-id>`.
2. Confirm the CLI's final summary shows a confirmed local sync path. If it
   doesn't, wait a minute for Drive for desktop to catch up and check that
   path manually (or check Drive for desktop's tray icon settings for your
   actual sync location if auto-detection failed).
3. Open DaVinci Resolve. Create a new project (or open an existing one) and
   import the master file from your local `Original/` folder into the Media
   Pool as you normally would.
4. Go to **Project Settings → Master Project Settings → General Options**
   and set **Proxy media path** to the local `Proxy/` folder path from step 2
   (the project-specific one, e.g. `...\Projects\YouTube_003\Proxy`).
5. Right-click the master clip in the Media Pool (or select all clips) and
   enable **Use Proxy Media** (or toggle proxy mode from the playback
   toolbar's proxy quality menu). Resolve should automatically recognize and
   link `<sessionId>_master.mov` in that folder to the original clip — no
   "Link Proxy Media" dialog needed.
6. Confirm: the clip's thumbnail/playback should now be visibly lower
   resolution (the proxy), scrubbing should feel noticeably lighter than
   scrubbing the original, and clicking a timestamp should show matching
   content between proxy and original (no offset).

### 2. If auto-relink doesn't happen

- **Check the filename stem matches exactly.** Open the Proxy folder and the
  Original folder side by side — `<sessionId>_master.mp4` and
  `<sessionId>_master.mov` should differ *only* in extension. If Phase 4 or
  5 was interrupted/retried and produced a differently-named file, that's a
  convention mismatch, not a Resolve problem.
- **Check the Proxy media path is set to the right folder** (Project
  Settings, not a global preference) and that it's the *local* filesystem
  path Drive for desktop syncs to, not a raw Drive URL.
- **Check the file actually synced locally** — if Drive for desktop hasn't
  finished syncing yet, the proxy file won't exist at that local path at
  all; Resolve can't link to a file that isn't there yet regardless of
  naming. Re-check after a few minutes, or manually verify file existence.
- **If the filename and path both check out and it still doesn't link**,
  try Resolve's manual **right-click clip → Link Proxy Media → select
  folder** once, pointed at the same Proxy folder — if that manual path
  works but auto-detection via the Proxy media path setting doesn't, that
  points to a Resolve-version-specific quirk in the auto-matching behavior
  rather than a problem with the generated file itself, worth flagging back
  since it would mean the convention needs adjusting for your Resolve
  version specifically.

---

# Phase 6: desktop companion (background orchestration)

A system tray app (`companion_app.py`) that watches Drive for sessions the phone
app has finished uploading, and automatically chains Phase 4 reconstruction into
Phase 5 proxy generation — so you don't run `reconstruct` and `generate-proxy` by
hand after every recording. `drive_manager.py`'s commands still work completely
independently; the tray app is an orchestration layer on top, not a replacement.

```
python companion_app.py
```

## Design decisions

**Tray app, not a Windows service.** A Windows service runs in Session 0 with no
UI access at all — it could not show status even in principle, which is the
opposite of "favor visibility." A tray app gives an always-visible status icon
(color-coded: green = idle/nothing waiting, amber = actively working, red = a
session needs your attention) plus a full dashboard window on click, at no real
cost for a single-user local app.

**Session-complete signal: a Drive marker file, not an idle-timeout guess.** The
phone app (Phase 3 addition) writes `<sessionId>_complete.json` — tagged
`appProperties: {sessionId, kind: session_complete, projectName, chunkCount,
totalBytes}` — once every chunk of a *stopped* recording session is confirmed
uploaded. The companion only ever auto-triggers reconstruction for a session once
this marker exists, so it's deterministic: no risk of firing mid-recording, no
"was that pause 15 minutes or is upload just slow" guessing a pure idle-timeout
approach would have.

**Local state: SQLite (`companion_state.db`, stdlib `sqlite3`), not a database
server.** Justified specifically because session stage needs to be (a) queryable
by stage — "everything AWAITING_RECONSTRUCTION" — and (b) durable across a
restart with zero special-case recovery code: the poll loop just reads whatever
each session's row currently says. A flat JSON file would need hand-rolled atomic
writes to be crash-safe against the same failure modes SQLite already handles;
that's infrastructure this single-user tool doesn't need to build itself.

**Restart safety falls out of the design, it isn't a special code path.** Every
session's pipeline stage lives in SQLite, not in memory. Closing the app mid-
reconstruction just leaves that session's row at `RECONSTRUCTING` (or wherever it
was); relaunching starts the same poll loop, which picks that row up again on its
next pass exactly as if nothing had happened — restart handling is "there is no
special restart handling," not an added feature.

**One session processed at a time, on one background thread.** No concurrency
here deliberately: this is a low-volume single-user tool, and running two ffmpeg
jobs at once would only contend for the same CPU/disk. On success, reconstruction
chains straight into proxy generation for that session rather than waiting for
the next 30s poll — nothing is gained by deferring a step that's already proven
Drive/ffmpeg are working.

**Manual overrides operate on local state only, never bypass the completeness
check.** Pause/Resume just sets a flag the poll loop skips. Retry resets a
`FAILED_*` session back to the `AWAITING_*` stage before it. "Process now" runs
the next step immediately instead of waiting for the poll interval — it still
only works on a session already in an `AWAITING_*` stage, i.e. one whose Drive
marker already proved upload was complete; there's no "start reconstruction on a
still-uploading session" button, since that would produce exactly the corrupt/
incomplete master Phase 4's own validation exists to prevent.

**Known race (accepted, not worked around):** if you click "Process now" on a
session in the same instant the 30-second poll picks it up too, both could start
reconstructing it concurrently. Phase 4's own idempotency guard
(`MasterAlreadyExistsError`) means the loser fails loudly rather than corrupting
anything — a duplicate-work edge case, not a duplicate-data one. Not worth adding
a lock for in a single-user tool for how rarely the timing would actually collide.

**Android-side cost of this phase:** the phone app's local Room database schema
changed (a new `sessions` table) and uses `fallbackToDestructiveMigration()`
rather than a hand-written migration. Any chunk sitting `PENDING`/`FAILED` in the
upload queue at the moment you update the phone app gets dropped from that
database (the video file itself is untouched on disk, but you'd need to
re-trigger its upload manually, or just re-record). Reasonable given this is
still active cross-phase development, not long-lived production queue state.

## Setup

No new Google Cloud setup — reuses Phase 1's `credentials.json`/`token.json`.

Run `python companion_app.py` once to confirm it launches (a tray icon should
appear). To have it start automatically when you log into Windows:

1. Press `Win+R`, type `shell:startup`, Enter — opens your Startup folder.
2. Create a shortcut there targeting:
   `pythonw.exe C:\path\to\CloudRecorder\companion_app.py`
   (use `pythonw.exe`, not `python.exe` — it runs without popping up a console
   window, matching the tray app's own "background, but visible via the icon"
   design).

## Tray UI: what each state means

- **Icon color:** green = nothing waiting on you, everything either idle or
  `READY`. Amber = actively reconstructing or generating a proxy right now. Red =
  at least one session is `FAILED_RECONSTRUCTION` or `FAILED_PROXY` — open the
  dashboard.
- **Tray menu status line:** `N ready · N working · N waiting · N failed` — a
  glance without opening the dashboard.
- **Dashboard table**, one row per session: session ID, project, current stage
  (`Awaiting reconstruction` / `Reconstructing...` / `Awaiting proxy generation` /
  `Generating proxy...` / `Ready to edit` / `FAILED (reconstruction)` /
  `FAILED (proxy generation)`, `(paused)` suffix if paused), last-updated
  timestamp, and the error message for any failed session.
- **Recent activity log** at the bottom of the dashboard — every step the
  orchestrator takes, per session, in plain text (mirrors what
  `reconstruct`/`generate-proxy` print when run manually).

## Test protocol

### 1. Normal end-to-end flow

1. Launch `python companion_app.py` — confirm the tray icon appears (green,
   nothing to do yet).
2. Record a short session on the phone app and let it fully upload (Upload
   status card shows all chunks `Uploaded`).
3. Within ~30s (the poll interval), the tray icon should turn amber and the
   dashboard should show the session appear at `Awaiting reconstruction`, then
   `Reconstructing...`, then `Awaiting proxy generation`, then
   `Generating proxy...`, then `Ready to edit` — with matching lines in the
   activity log at each step, and the icon returning to green once nothing is
   left waiting.
4. Confirm in Drive: chunks gone from `Original/` (deleted post-reconstruction),
   `<sessionId>_master.mp4` present, a proxy `.mov` in `Proxy/`.

### 2. The main scenario you asked for: close mid-upload, relaunch, confirm resume

1. Start a recording on the phone with a short chunk length (5s), so uploads are
   frequent and easy to catch mid-flight.
2. **Before** the phone finishes uploading everything (i.e. before the phone
   writes its completion marker — check the phone's Upload status card still
   shows `Pending`/`Uploading`), stop the phone recording, then close the
   companion app entirely (Quit from the tray menu, not just closing the
   dashboard window).
3. Let the phone finish uploading all its chunks and writing its completion
   marker fully in the background (the phone app doesn't need the companion
   running for this — Phase 3's upload queue is independent).
4. Relaunch `python companion_app.py`.
5. **Check:** within moments (the immediate startup poll, not waiting the full
   30s), the dashboard shows that session appear directly at
   `Awaiting reconstruction` (its marker was already sitting in Drive, unseen
   until now), and it proceeds through reconstruction and proxy generation
   exactly as in test 1 — nothing was lost, nothing needed manual re-triggering.

### 3. Manual overrides

1. While a session is `Awaiting reconstruction`, select it in the dashboard and
   click **Pause** — confirm it stays at that stage indefinitely across poll
   cycles (does not silently start processing).
2. Click **Resume** — confirm it picks up on the next poll (or click
   **Process now** to skip the wait).
3. Manually delete a chunk from a session's `Original/` folder in Drive *before*
   its marker would exist — not reachable through the companion normally since a
   marker only appears once every chunk is confirmed uploaded, so this tests
   Phase 4's own validation path: manually run `python drive_manager.py
   reconstruct <id>` for a session with a deliberately broken chunk sequence
   (per Phase 4's own test protocol above) and confirm the companion, if it also
   discovers that session's marker, correctly reflects `FAILED (reconstruction)`
   with the missing-chunk error visible in the dashboard, and that **Retry**
   after fixing the gap successfully re-processes it.

### What "working correctly" looks like

Sessions move through the dashboard's stages without you touching
`drive_manager.py` at all, mid-upload closures never lose a session (it just
resumes at the correct stage after relaunch), and any real failure shows up as a
red icon with a specific, actionable error in the dashboard — never a silent gap
you'd only discover later when a proxy you expected isn't there.

## What Phase 6 does not do (by design)

No DaVinci Resolve project file automation (Phase 7) — this phase's job ends the
moment a validated proxy is sitting synced and ready to edit.

---

# Phase 7: DaVinci Resolve integration

Given a session the companion app has marked `READY` (Phase 6), `open-resolve`
creates that session's DaVinci Resolve project, imports the original, explicitly
links the proxy, best-effort sets "Prefer Proxies," and builds a starter timeline
— reducing your setup clicks before you start editing normally, not replacing
Resolve's own interface.

```
python drive_manager.py open-resolve <session-id>
```

Also available from the companion app's dashboard: select a `Ready to edit`
session, click **Open in Resolve**.

**Project structure (your choice, confirmed before building): one Resolve
project per recording session**, not one shared project per series. Each
session gets its own project named `<projectName> - <sessionId>`. Clicking
**Open in Resolve** again for the same session reopens that same project
(`CreateProject` returns `None` for a name that already exists, at which point
the code falls back to `LoadProject`) rather than duplicating it.

## Run this first: `resolve-probe`

```
python drive_manager.py resolve-probe
```

Read the honesty section at the top of `pipeline/resolve_bridge.py` for the
full reasoning, but the short version: I could not test any of this against a
real DaVinci Resolve install from where this was built (no Windows machine, no
Resolve here). What's implemented is split into two tiers:

**Built on confirmed API behavior** (per Blackmagic's published scripting
docs, stable since Resolve 16/17, free version included — not Studio-gated):
project creation/loading, Media Pool import, `MediaPoolItem.LinkProxyMedia()`
for explicit proxy linking (checked via its actual boolean return, not
assumed), and timeline creation.

**Not independently verifiable without your machine**, handled defensively:
whether "Prefer Proxies" is exposed via `SetSetting()` at all in your specific
Resolve version — Blackmagic doesn't publish a full settings-key list, so
`resolve-probe` empirically dumps `GetSetting("")` and searches for keys
containing "proxy" rather than a script assuming a hardcoded key name is
correct. **Run `resolve-probe` once and read its output** before trusting the
"Prefer Proxies" checklist line — if it reports no usable proxy key was found,
that's real information (not a bug to fix), and the one manual click documented
below is what you'll do instead every time.

## Required one-time manual setup (this code cannot do this for you)

1. **Resolve → Preferences → General → "External scripting using"** must be
   set to **Local** (often defaults to "None"). Without this, no external
   Python process — including this one — can connect to Resolve's scripting
   API at all, regardless of anything else being configured correctly.
2. Set these three environment variables (System Properties → Environment
   Variables, or your shell profile) before running any `drive_manager.py`
   command that touches Resolve:
   ```
   RESOLVE_SCRIPT_API=%PROGRAMDATA%\Blackmagic Design\DaVinci Resolve\Support\Developer\Scripting
   RESOLVE_SCRIPT_LIB=C:\Program Files\Blackmagic Design\DaVinci Resolve\fusionscript.dll
   PYTHONPATH=%PYTHONPATH%;%RESOLVE_SCRIPT_API%\Modules
   ```
   (Adjust paths if Resolve is installed somewhere non-default.) `resolve-probe`
   fails with these exact instructions printed if this isn't set up yet, rather
   than a bare import traceback.
3. If Resolve is installed somewhere other than the default
   `C:\Program Files\Blackmagic Design\DaVinci Resolve\Resolve.exe`, set
   `CLOUDRECORDER_RESOLVE_EXE` to your actual path so this code can launch it
   when it isn't already running.

## What "Open in Resolve" actually does, step by step

1. Computes the expected local (Drive-for-desktop-synced) paths for the
   session's original and proxy from the same `<sessionId>_master.mp4`/`.mov`
   naming convention Phases 4/5 already use — no extra Drive API call needed.
2. Waits briefly for both files to actually exist at those local paths (Drive
   for desktop sync can lag behind the Drive-side upload) — reports clearly if
   they're not there rather than failing on a missing-file error deeper in.
3. Connects to a running Resolve, or launches `Resolve.exe` and polls for the
   connection (up to 90s) if it wasn't already open.
4. Creates (or reopens) the session's project.
5. Imports the original into the Media Pool.
6. Calls `LinkProxyMedia()` on the imported clip and checks its actual return
   value — this is real proxy linking, verified, not "the file is present in
   the Proxy folder so it's probably linked."
7. Attempts to set "Prefer Proxies" (see the honesty section above); reports
   whether it actually succeeded.
8. Creates a new timeline and appends the imported clip(s) — footage ready to
   cut, no auto-editing.

Every one of these eight steps is independently recorded — a failure at step 3
doesn't silently skip steps 4-8, it stops and reports exactly steps 1-2 as done
and everything after as not attempted, so you always know precisely where
automation stopped and manual work should pick up.

## Test protocol

1. Get one session to `Ready to edit` in the companion app (Phase 6's own test
   protocol, or just let a real recording flow through normally).
2. Run `python drive_manager.py resolve-probe` once. Read its output — note
   whether it found a usable "proxy" settings key. This tells you which
   outcome to expect for the "Prefer Proxies" line below.
3. Run `python drive_manager.py open-resolve <session-id>` (or the dashboard
   button). Watch the printed checklist.

### Checklist: what should already be true when it finishes

- [ ] **Original media synced locally** — checked (this is a local disk check,
  should essentially always pass if the session reached `READY`).
- [ ] **Proxy media synced locally** — checked, same reasoning.
- [ ] **Connect to DaVinci Resolve** — checked once Resolve is up (launches it
  automatically if it wasn't running; give it up to 90s the first time).
- [ ] **Create or open Resolve project** — a new project named
  `<projectName> - <sessionId>` exists in Resolve's Project Manager (or the
  existing one reopened, on a repeat run).
- [ ] **Import original media into Media Pool** — the master clip is visible
  in the project's Media Pool.
- [ ] **Link proxy media** — checked *only if* `LinkProxyMedia()` returned
  `True`. **If this shows unchecked**, right-click the clip in Resolve's Media
  Pool → **Link Proxy Media** → select the `.mov` file in the local `Proxy/`
  folder, manually, once. This is exactly the "flag it clearly rather than
  assume it worked" behavior the phase asked for.
- [ ] **Set 'Prefer Proxies' for this project** — checked only if a working
  settings key was actually found and verified by reading it back. **If
  unchecked** (very possible — see the honesty section above): Resolve's
  **Playback menu → Proxy Handling → Prefer Proxies**, one click, applies
  Resolve-wide rather than per-project anyway.
- [ ] **Create timeline with clip(s)** — a new timeline named
  `<sessionId> - Timeline` exists with the clip already on it, ready to edit.

### What you should still expect to do manually, always

Actual editing, obviously — this phase deliberately builds no cutting/
arranging intelligence. And per the checklist above: possibly one manual
proxy-relink click, and near-certainly one manual "Prefer Proxies" toggle
(Resolve-wide, one-time — not per session) unless `resolve-probe` found a
working scriptable key on your version.

## What's still missing for true end-to-end automation (Phase 8 territory)

Phase 8 was described as "should mostly fall out of Phases 1-7 working
together." Based on what actually got built, here's what I think is genuinely
still missing rather than already covered:

- **No trigger from "Open in Resolve" back to a fully unattended flow** — a
  human still has to click it per session (by design, matching your
  one-project-per-session choice and the "don't build editing tools" limit).
  If true zero-touch end-to-end is the Phase 8 goal, that click is the one
  remaining manual step in the entire pipeline, and it's worth deciding
  explicitly in Phase 8 whether "Open in Resolve" should also auto-fire the
  moment a session reaches `READY`, or whether opening your NLE automatically
  without you asking is actually not something you'd want.
- **The "Prefer Proxies" gap is a real open question**, not just a Phase 7
  loose end — until `resolve-probe` is actually run against your Resolve
  install, we don't know whether that setting can be automated at all on your
  version. Phase 8 shouldn't assume either answer.
- **Multi-session videos**: since this phase deliberately chose
  one-project-per-session, a YouTube video built from several recording
  sessions still needs you to manually combine those projects/timelines in
  Resolve. If that's a real workflow you'll hit often, it's worth an explicit
  decision (not a silent assumption) whether a future phase should support
  merging sessions into one project instead.
