"""Google Drive client — service-account auth + Changes API (CM-34).

Jira: CM-34  | Epic: CM-Epic 9  | Phase 1

A headless timer job can't run an interactive 3-legged OAuth flow, so this
uses a Google **service account** (JWT bearer — still OAuth2). The SA's JSON
key lives in Key Vault (``google-drive-sa-key``) and the watched Drive folder
is shared with the SA's email. See ``docs/INFRA.md``.

Delta detection rides the Drive **Changes API**: a persisted ``startPageToken``
(stored in Cosmos by the orchestrator) is replayed each run via
``changes.list`` so only modified docs come back. The first run has no token,
so :func:`list_folder` enumerates the folder once and
:func:`get_start_page_token` captures the cursor for subsequent runs.

The google-* libraries are an optional, deploy-time dependency (declared in
the ``gdrive`` extra + the Function App's ``requirements.txt``); they're
lazy-imported here so ``import agents.knowledge`` works without them
installed — tests drive the orchestrator through a fake that satisfies the
same structural Protocol.
"""

from __future__ import annotations

import json
import os
from typing import Any

from agents.knowledge.models import SECRET_PLACEHOLDER, DriveChange

#: Read-only Drive access is all the sync needs.
DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]
#: Native Google Docs must be exported (not media-downloaded) to get text.
GOOGLE_DOC_MIME = "application/vnd.google-apps.document"


class GoogleDriveClient:
    """Thin wrapper over the Drive v3 API surface the sync job needs."""

    def __init__(self, service: Any) -> None:
        self._service = service

    @classmethod
    def from_service_account_info(
        cls,
        info: dict[str, Any],
        *,
        scopes: list[str] | None = None,
    ) -> GoogleDriveClient:
        """Build from a parsed service-account JSON key."""
        from google.oauth2 import service_account  # noqa: PLC0415
        from googleapiclient.discovery import build  # noqa: PLC0415

        # Bind through Any aliases so mypy doesn't flag the (sometimes
        # unannotated) google-* SDK surface under strict mode — these libs are
        # an optional, deploy-time extra and may or may not be present.
        credentials_cls: Any = service_account.Credentials
        build_fn: Any = build
        creds = credentials_cls.from_service_account_info(
            info, scopes=scopes or DRIVE_SCOPES
        )
        # cache_discovery=False avoids the noisy file-cache warning under a
        # read-only Functions filesystem.
        service = build_fn("drive", "v3", credentials=creds, cache_discovery=False)
        return cls(service)

    @classmethod
    def from_env(cls) -> GoogleDriveClient | None:
        """Build from ``GOOGLE_DRIVE_SA_KEY`` (KV-sourced JSON), or ``None``.

        Returns ``None`` when the secret is unset or still the CM-18
        ``REPLACE-ME`` placeholder, so the Function App can boot before an
        operator populates the real key.
        """
        raw = os.environ.get("GOOGLE_DRIVE_SA_KEY", "").strip()
        if not raw or raw == SECRET_PLACEHOLDER:
            return None
        return cls.from_service_account_info(json.loads(raw))

    def get_start_page_token(self) -> str:
        """Current Changes-API cursor — captured at the end of a bootstrap run."""
        resp = self._service.changes().getStartPageToken().execute()
        return str(resp["startPageToken"])

    def list_changes(self, page_token: str) -> tuple[list[DriveChange], str]:
        """All changes since ``page_token``; returns (changes, next_token).

        Pages through the Changes API; trashed/removed files surface with
        ``removed=True`` so the orchestrator can purge their chunks.
        """
        changes: list[DriveChange] = []
        token = page_token
        while True:
            resp = (
                self._service.changes()
                .list(
                    pageToken=token,
                    spaces="drive",
                    includeRemoved=True,
                    fields=(
                        "nextPageToken,newStartPageToken,"
                        "changes(fileId,removed,file(name,mimeType,trashed))"
                    ),
                )
                .execute()
            )
            for ch in resp.get("changes", []):
                file = ch.get("file") or {}
                removed = bool(ch.get("removed")) or bool(file.get("trashed"))
                changes.append(
                    DriveChange(
                        file_id=str(ch.get("fileId", "")),
                        title=str(file.get("name", "")),
                        mime_type=str(file.get("mimeType", "")),
                        removed=removed,
                    )
                )
            next_page = resp.get("nextPageToken")
            if next_page:
                token = str(next_page)
                continue
            return changes, str(resp["newStartPageToken"])

    def list_folder(self, folder_id: str) -> list[DriveChange]:
        """Enumerate non-trashed files directly under ``folder_id`` (bootstrap)."""
        results: list[DriveChange] = []
        token: str | None = None
        while True:
            resp = (
                self._service.files()
                .list(
                    q=f"'{folder_id}' in parents and trashed=false",
                    spaces="drive",
                    fields="nextPageToken,files(id,name,mimeType)",
                    pageToken=token,
                )
                .execute()
            )
            for f in resp.get("files", []):
                results.append(
                    DriveChange(
                        file_id=str(f["id"]),
                        title=str(f.get("name", "")),
                        mime_type=str(f.get("mimeType", "")),
                        removed=False,
                    )
                )
            token = resp.get("nextPageToken")
            if not token:
                return results

    def export_text(self, change: DriveChange) -> str:
        """Extract plain text for a doc (export Google Docs, media others)."""
        if change.mime_type == GOOGLE_DOC_MIME:
            data = (
                self._service.files()
                .export(fileId=change.file_id, mimeType="text/plain")
                .execute()
            )
        else:
            data = self._service.files().get_media(fileId=change.file_id).execute()
        if isinstance(data, bytes):
            return data.decode("utf-8", errors="replace")
        return str(data)
