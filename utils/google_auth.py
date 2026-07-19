"""Google API authentication helper.

Supports two credential types based on what's in your credentials file:
  - Service Account JSON  (recommended for automation / headless servers)
  - OAuth2 Desktop App credentials  (requires browser login on first run)

The credential type is auto-detected from the JSON file's ``type`` field.

Usage
-----
    from utils.google_auth import build_services

    sheets_service, drive_service = build_services()
"""

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def _load_credentials_file(path: str):
    """Return the parsed JSON from a credentials file, or raise a helpful error."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(
            f"Credentials file not found: {path}\n"
            "Set GOOGLE_CREDENTIALS_FILE in your .env to the correct path."
        )
    with open(p, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _build_service_account_creds(creds_path: str):
    """Build credentials from a Service Account JSON key file."""
    from google.oauth2 import service_account

    creds = service_account.Credentials.from_service_account_file(
        creds_path, scopes=_SCOPES
    )
    logger.debug("Authenticated via Service Account: %s", creds.service_account_email)
    return creds


def _build_oauth2_creds(creds_path: str, token_path: str = "token.json"):
    """Build credentials via OAuth2 Desktop flow (opens browser on first run)."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    creds = None
    token_p = Path(token_path)

    # Load existing token if it exists
    if token_p.exists():
        creds = Credentials.from_authorized_user_file(str(token_p), _SCOPES)

    # Refresh or obtain new token
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            logger.debug("Refreshing expired OAuth2 token")
            creds.refresh(Request())
        else:
            logger.info("Opening browser for Google OAuth2 login...")
            flow = InstalledAppFlow.from_client_secrets_file(creds_path, _SCOPES)
            creds = flow.run_local_server(port=0)
        # Persist the token for future runs
        with open(token_path, "w", encoding="utf-8") as fh:
            fh.write(creds.to_json())
        logger.info("OAuth2 token saved to %s", token_path)

    return creds


def build_credentials(creds_path: str | None = None, token_path: str = "token.json"):
    """
    Auto-detect credential type and return a google.auth Credentials object.

    Parameters
    ----------
    creds_path : str, optional
        Path to the credentials JSON file. Falls back to the
        ``GOOGLE_CREDENTIALS_FILE`` environment variable, then ``credentials.json``.
    token_path : str
        Where to cache the OAuth2 token (only used for OAuth2 Desktop flow).
    """
    if creds_path is None:
        creds_path = os.environ.get("GOOGLE_CREDENTIALS_FILE", "credentials.json")

    data = _load_credentials_file(creds_path)
    cred_type = data.get("type", "")

    if cred_type == "service_account":
        logger.debug("Detected Service Account credentials")
        return _build_service_account_creds(creds_path)
    elif cred_type in ("authorized_user", ""):
        if "installed" in data or "web" in data:
            logger.debug("Detected OAuth2 Desktop client_secret credentials")
            return _build_oauth2_creds(creds_path, token_path)
        elif "refresh_token" in data or cred_type == "authorized_user":
            logger.debug("Detected already-authorised OAuth2 token")
            return _build_oauth2_creds(creds_path, token_path)
        else:
            raise ValueError(
                f"Cannot determine credential type from {creds_path}. "
                "Expected a Service Account JSON or an OAuth2 client_secret JSON."
            )
    else:
        raise ValueError(
            f"Unknown credential type '{cred_type}' in {creds_path}. "
            "Supported types: service_account, authorized_user."
        )


def build_services(creds_path: str | None = None, token_path: str = "token.json"):
    """
    Build and return (sheets_service, drive_service) API client instances.

    Example
    -------
        sheets, drive = build_services()
        sheets.spreadsheets().values().get(spreadsheetId=sid, range='A1').execute()
    """
    from googleapiclient.discovery import build

    creds = build_credentials(creds_path=creds_path, token_path=token_path)

    sheets_service = build("sheets", "v4", credentials=creds, cache_discovery=False)
    drive_service = build("drive", "v3", credentials=creds, cache_discovery=False)

    logger.debug("Google Sheets and Drive services initialised")
    return sheets_service, drive_service
