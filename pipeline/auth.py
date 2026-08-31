"""OAuth2 authentication for the Google Drive API.

Uses the installed-app loopback flow. Credentials are read from
``credentials.json`` (downloaded from Google Cloud Console) and the
resulting token, including refresh token, is cached in ``token.json``
so subsequent runs do not require re-authenticating in a browser.

Both files live in the project root by default and are gitignored.
"""

import socket
from pathlib import Path

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from pipeline.errors import AuthError, NetworkError

# drive.file: the app can only see/manage files and folders it creates
# itself (or that the user explicitly opens with it). It cannot browse
# or read the rest of the user's Drive. Sufficient for Phase 1, which
# only ever creates and lists folders it owns.
SCOPES = ["https://www.googleapis.com/auth/drive.file"]

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CREDENTIALS_PATH = PROJECT_ROOT / "credentials.json"
TOKEN_PATH = PROJECT_ROOT / "token.json"


def get_credentials() -> Credentials:
    """Return valid credentials, refreshing or running the OAuth flow as needed."""
    creds = None

    if TOKEN_PATH.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)
        except ValueError as exc:
            raise AuthError(
                f"token.json at {TOKEN_PATH} is corrupt or unreadable ({exc}). "
                "Delete it and re-run to re-authenticate."
            ) from exc

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except RefreshError as exc:
            raise AuthError(
                "Your Google Drive authorization has expired or been revoked. "
                "Delete token.json and re-run to re-authenticate:\n"
                f"  {TOKEN_PATH}"
            ) from exc
        except (socket.gaierror, ConnectionError, OSError) as exc:
            raise NetworkError(
                "Could not reach Google to refresh your access token. "
                "Check your internet connection and try again."
            ) from exc
        _save_token(creds)
        return creds

    # No usable cached credentials: run the interactive consent flow.
    if not CREDENTIALS_PATH.exists():
        raise AuthError(
            f"No credentials.json found at {CREDENTIALS_PATH}.\n"
            "Download OAuth client credentials (type: Desktop app) from the "
            "Google Cloud Console and place the file there. See README.md."
        )

    try:
        flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_PATH), SCOPES)
        creds = flow.run_local_server(port=0)
    except (socket.gaierror, ConnectionError, OSError) as exc:
        raise NetworkError(
            "Could not reach Google to complete authentication. "
            "Check your internet connection and try again."
        ) from exc

    _save_token(creds)
    return creds


def _save_token(creds: Credentials) -> None:
    TOKEN_PATH.write_text(creds.to_json())
