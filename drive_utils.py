"""Google Drive upload helper for the GODTIER render pipeline.

Env vars:
  GOOGLE_DRIVE_CREDENTIALS — full service account JSON (as a string)
  OUTPUT_FOLDER_ID         — target folder id
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

SCOPES = ["https://www.googleapis.com/auth/drive"]


def _service():
    creds_str = os.environ["GOOGLE_DRIVE_CREDENTIALS"]
    creds_info = json.loads(creds_str)
    creds = service_account.Credentials.from_service_account_info(
        creds_info, scopes=SCOPES
    )
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def upload_to_drive(local_path: Path, filename: str) -> dict:
    """Upload local_path to OUTPUT_FOLDER_ID with the given filename.

    Returns {id, webViewLink, webContentLink}. The file is set to public-read
    so anyone with the link can view/download (the n8n workflow needs this so
    downstream nodes can hand the URL to YouTube/Notion/etc.).
    """
    svc = _service()
    folder_id = os.environ["OUTPUT_FOLDER_ID"]

    metadata = {"name": filename, "parents": [folder_id]}
    media = MediaFileUpload(str(local_path), mimetype="video/mp4", resumable=True)
    file = svc.files().create(
        body=metadata,
        media_body=media,
        fields="id, webViewLink, webContentLink",
        supportsAllDrives=True,
    ).execute()

    # Make link-accessible (anyone with the URL can view)
    svc.permissions().create(
        fileId=file["id"],
        body={"role": "reader", "type": "anyone"},
        supportsAllDrives=True,
    ).execute()

    return file
