# Cloud Content Pipeline — Phase 1: Google Drive Foundation

A small local CLI that authenticates with your personal Google account and
manages a project folder structure in your Google Drive:

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

This phase only handles authentication and folder management. Phone
recording, chunked upload, proxy generation, and Resolve integration are
later phases and are not implemented here.

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
- `pipeline/errors.py` — custom exceptions
- `credentials.json`, `token.json` — local secrets, gitignored, not included in this repo
