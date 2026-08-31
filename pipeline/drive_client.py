"""Thin wrapper around the Google Drive API v3.

Handles building the service, translating low-level errors (network,
auth, quota) into the pipeline's own exception types, and retrying
transient rate-limit errors with exponential backoff.
"""

import socket
import time

from google.auth.exceptions import RefreshError
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from pipeline.auth import get_credentials
from pipeline.errors import AuthError, NetworkError, QuotaError

FOLDER_MIME_TYPE = "application/vnd.google-apps.folder"

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
