"""Thin wrapper around the Google Drive API v3.

Handles building the service, translating low-level errors (network,
auth, quota) into the pipeline's own exception types, and retrying
transient rate-limit errors with exponential backoff.
"""

import socket
import time
from pathlib import Path

from google.auth.exceptions import RefreshError
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

from pipeline.auth import get_credentials
from pipeline.errors import AuthError, NetworkError, QuotaError

FOLDER_MIME_TYPE = "application/vnd.google-apps.folder"
_CHUNK_NUM_RETRIES = 5

_RETRYABLE_STATUS_CODES = {403, 429, 500, 502, 503}
_MAX_RETRIES = 4
_INITIAL_BACKOFF_SECONDS = 1.0


class DriveClient:
    def __init__(self):
        try:
            creds = get_credentials()
        except (RefreshError,) as exc:
            raise AuthError(f"Google Drive authentication failed: {exc}") from exc
        self._service = build("drive", "v3", credentials=creds)

    def _execute(self, request):
        """Execute a Drive API request with retry/backoff and clear error messages."""
        backoff = _INITIAL_BACKOFF_SECONDS
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                return request.execute()
            except HttpError as exc:
                status = exc.resp.status if exc.resp is not None else None
                reason = _extract_reason(exc)

                if status == 401:
                    raise AuthError(
                        "Google rejected the request as unauthorized. Your token may "
                        "have been revoked. Delete token.json and re-run to "
                        "re-authenticate."
                    ) from exc

                is_quota = status == 429 or (
                    status == 403 and reason in ("rateLimitExceeded", "userRateLimitExceeded", "quotaExceeded")
                )
                if is_quota or status in _RETRYABLE_STATUS_CODES:
                    if attempt == _MAX_RETRIES:
                        if is_quota:
                            raise QuotaError(
                                "Google Drive API rate limit or quota exceeded, even "
                                f"after {_MAX_RETRIES} retries. Wait a while and try "
                                "again."
                            ) from exc
                        raise NetworkError(
                            f"Google Drive API returned a server error (HTTP {status}) "
                            f"after {_MAX_RETRIES} retries. Try again later."
                        ) from exc
                    time.sleep(backoff)
                    backoff *= 2
                    continue

                raise NetworkError(f"Google Drive API request failed: {exc}") from exc
            except (socket.gaierror, ConnectionError, OSError) as exc:
                if attempt == _MAX_RETRIES:
                    raise NetworkError(
                        "Could not reach Google Drive. Check your internet "
                        "connection and try again."
                    ) from exc
                time.sleep(backoff)
                backoff *= 2
        raise NetworkError("Google Drive API request failed after retries.")

    def find_folder(self, name: str, parent_id: str | None) -> dict | None:
        """Return the first folder matching name/parent, or None if not found."""
        query_parts = [
            f"name = '{_escape(name)}'",
            f"mimeType = '{FOLDER_MIME_TYPE}'",
            "trashed = false",
        ]
        if parent_id:
            query_parts.append(f"'{parent_id}' in parents")
        else:
            query_parts.append("'root' in parents")

        request = self._service.files().list(
            q=" and ".join(query_parts),
            spaces="drive",
            fields="files(id, name)",
            pageSize=1,
        )
        result = self._execute(request)
        files = result.get("files", [])
        return files[0] if files else None

    def create_folder(self, name: str, parent_id: str | None) -> dict:
        """Create a folder and return its Drive metadata (id, name)."""
        metadata = {"name": name, "mimeType": FOLDER_MIME_TYPE}
        if parent_id:
            metadata["parents"] = [parent_id]
        request = self._service.files().create(body=metadata, fields="id, name")
        return self._execute(request)

    def ensure_folder(self, name: str, parent_id: str | None) -> dict:
        """Return the existing folder if present, otherwise create it."""
        existing = self.find_folder(name, parent_id)
        if existing:
            return existing
        return self.create_folder(name, parent_id)

    def list_subfolders(self, parent_id: str) -> list[dict]:
        """Return metadata for all non-trashed subfolders of parent_id."""
        folders = []
        page_token = None
        query = (
            f"'{parent_id}' in parents and "
            f"mimeType = '{FOLDER_MIME_TYPE}' and trashed = false"
        )
        while True:
            request = self._service.files().list(
                q=query,
                spaces="drive",
                fields="nextPageToken, files(id, name)",
                pageSize=100,
                pageToken=page_token,
            )
            result = self._execute(request)
            folders.extend(result.get("files", []))
            page_token = result.get("nextPageToken")
            if not page_token:
                break
        return folders

    def find_files_by_app_property(self, key: str, value: str, fields: str) -> list[dict]:
        """Return metadata for all non-trashed files tagged with the given custom
        appProperties key/value (used to find a session's chunks regardless of which
        project folder they live in)."""
        files: list[dict] = []
        page_token = None
        query = f"appProperties has {{ key='{_escape(key)}' and value='{_escape(value)}' }} and trashed = false"
        while True:
            request = self._service.files().list(
                q=query,
                spaces="drive",
                fields=f"nextPageToken, files({fields})",
                pageSize=100,
                pageToken=page_token,
            )
            result = self._execute(request)
            files.extend(result.get("files", []))
            page_token = result.get("nextPageToken")
            if not page_token:
                break
        return files

    def find_file(self, name: str, parent_id: str) -> dict | None:
        """Return the first non-folder file matching name/parent, or None."""
        query = (
            f"name = '{_escape(name)}' and '{parent_id}' in parents and "
            f"trashed = false and mimeType != '{FOLDER_MIME_TYPE}'"
        )
        request = self._service.files().list(
            q=query, spaces="drive", fields="files(id, name)", pageSize=1,
        )
        result = self._execute(request)
        found = result.get("files", [])
        return found[0] if found else None

    def download_file(self, file_id: str, destination: Path) -> None:
        """Downloads a file's content to destination, retrying transient chunk
        failures. Raises the same NetworkError/QuotaError/AuthError as other calls."""
        request = self._service.files().get_media(fileId=file_id)
        with open(destination, "wb") as handle:
            downloader = MediaIoBaseDownload(handle, request)
            done = False
            while not done:
                try:
                    _, done = downloader.next_chunk(num_retries=_CHUNK_NUM_RETRIES)
                except HttpError as exc:
                    status = exc.resp.status if exc.resp is not None else None
                    if status == 401:
                        raise AuthError(
                            "Google rejected the download as unauthorized. Delete "
                            "token.json and re-run to re-authenticate."
                        ) from exc
                    raise NetworkError(f"Failed downloading file {file_id}: {exc}") from exc

    def upload_file(
        self,
        local_path: Path,
        parent_id: str,
        name: str,
        mime_type: str = "video/mp4",
        progress_callback=None,
        app_properties: dict[str, str] | None = None,
    ) -> str:
        """Resumable upload of a local file to Drive. Returns the new file's id."""
        metadata = {"name": name, "parents": [parent_id]}
        if app_properties:
            metadata["appProperties"] = app_properties
        media = MediaFileUpload(str(local_path), mimetype=mime_type, resumable=True, chunksize=8 * 1024 * 1024)
        request = self._service.files().create(body=metadata, media_body=media, fields="id")

        response = None
        while response is None:
            try:
                status, response = request.next_chunk(num_retries=_CHUNK_NUM_RETRIES)
            except HttpError as exc:
                http_status = exc.resp.status if exc.resp is not None else None
                if http_status == 401:
                    raise AuthError(
                        "Google rejected the upload as unauthorized. Delete "
                        "token.json and re-run to re-authenticate."
                    ) from exc
                raise NetworkError(f"Failed uploading {name}: {exc}") from exc
            if status and progress_callback:
                progress_callback(status.progress())
        return response["id"]

    def get_file(self, file_id: str, fields: str = "id, name, parents") -> dict:
        request = self._service.files().get(fileId=file_id, fields=fields)
        return self._execute(request)

    def update_file_content(
        self,
        file_id: str,
        local_path: Path,
        mime_type: str = "video/mp4",
        progress_callback=None,
        app_properties: dict[str, str] | None = None,
    ) -> str:
        """Resumable upload that REPLACES an existing file's content in place,
        keeping the same file id (and Drive's own revision history) rather than
        creating a new file — used to append to a growing project master/proxy
        without leaving Resolve (which references by local synced path) pointing
        at an orphaned, deleted file id."""
        media = MediaFileUpload(str(local_path), mimetype=mime_type, resumable=True, chunksize=8 * 1024 * 1024)
        body = {"appProperties": app_properties} if app_properties else {}
        request = self._service.files().update(fileId=file_id, body=body, media_body=media, fields="id")

        response = None
        while response is None:
            try:
                status, response = request.next_chunk(num_retries=_CHUNK_NUM_RETRIES)
            except HttpError as exc:
                http_status = exc.resp.status if exc.resp is not None else None
                if http_status == 401:
                    raise AuthError(
                        "Google rejected the upload as unauthorized. Delete "
                        "token.json and re-run to re-authenticate."
                    ) from exc
                raise NetworkError(f"Failed updating file {file_id}: {exc}") from exc
            if status and progress_callback:
                progress_callback(status.progress())
        return response["id"]

    def delete_file(self, file_id: str) -> None:
        request = self._service.files().delete(fileId=file_id)
        self._execute(request)


def _extract_reason(exc: HttpError) -> str | None:
    try:
        errors = exc.error_details
        if errors and isinstance(errors, list):
            return errors[0].get("reason")
    except (AttributeError, IndexError, KeyError, TypeError):
        pass
    return None


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")
