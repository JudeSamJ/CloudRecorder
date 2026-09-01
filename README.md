# Cloud Content Pipeline — Desktop App (Phases 1 + 4)

A local CLI that authenticates with your personal Google account, manages a
project folder structure in your Google Drive, and (Phase 4) reassembles a
recording session's uploaded chunks into one validated master video:

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

Proxy generation and Resolve integration are later phases and are not
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
- `pipeline/ffmpeg_tools.py` — Phase 4: ffmpeg/ffprobe subprocess wrappers
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
