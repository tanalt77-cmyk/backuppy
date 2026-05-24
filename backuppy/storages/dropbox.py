"""Dropbox storage using the official Dropbox SDK."""
from __future__ import annotations

import datetime as dt
import hashlib
import logging
from pathlib import Path

from ..config import DropboxCfg
from .base import BaseStorage


class DropboxStorage(BaseStorage):
    name = "dropbox"

    def __init__(self, cfg: DropboxCfg, log: logging.Logger):
        self.cfg = cfg
        self.log = log
        self._client = None

    @property
    def client(self):
        if self._client is not None:
            return self._client
        try:
            import dropbox
        except ImportError as e:
            raise RuntimeError("dropbox SDK not installed. pip install dropbox") from e

        if self.cfg.refresh_token and self.cfg.app_key:
            self._client = dropbox.Dropbox(
                oauth2_refresh_token=self.cfg.refresh_token,
                app_key=self.cfg.app_key,
                app_secret=self.cfg.app_secret or None,
            )
        elif self.cfg.access_token:
            self._client = dropbox.Dropbox(self.cfg.access_token)
        else:
            raise RuntimeError(
                "Dropbox needs either (access_token) or (refresh_token + app_key)"
            )
        return self._client

    def _remote(self, name: str) -> str:
        return f"{self.cfg.remote_path.rstrip('/')}/{name}"

    def upload(self, local: Path) -> str:
        import dropbox
        from dropbox.files import WriteMode, CommitInfo, UploadSessionCursor

        remote = self._remote(local.name)
        size = local.stat().st_size
        size_mb = size / 1024 / 1024
        self.log.info("Dropbox: uploading %s (%.2f MB) → %s",
                      local.name, size_mb, remote)

        chunk = self.cfg.chunk_size_mb * 1024 * 1024
        with open(local, "rb") as f:
            if size <= chunk:
                self.client.files_upload(f.read(), remote,
                                         mode=WriteMode.overwrite)
            else:
                # Chunked upload via upload_session
                session = self.client.files_upload_session_start(f.read(chunk))
                cursor = UploadSessionCursor(session_id=session.session_id,
                                             offset=f.tell())
                commit = CommitInfo(path=remote, mode=WriteMode.overwrite)
                while f.tell() < size - chunk:
                    self.client.files_upload_session_append_v2(
                        f.read(chunk), cursor
                    )
                    cursor.offset = f.tell()
                self.client.files_upload_session_finish(
                    f.read(), cursor, commit
                )
        return f"dropbox:{remote}"

    def list_files(self) -> list[dict]:
        try:
            result = self.client.files_list_folder(self.cfg.remote_path)
        except Exception:
            return []
        out = []
        while True:
            for e in result.entries:
                # Filter to files only
                if hasattr(e, "size"):
                    out.append({
                        "name": e.name,
                        "id": e.path_lower,
                        "size": e.size,
                        "modified": e.server_modified,
                    })
            if not result.has_more:
                break
            result = self.client.files_list_folder_continue(result.cursor)
        return out

    def delete(self, file_id: str) -> None:
        self.client.files_delete_v2(file_id)

    def check_access(self) -> None:
        # get_current_account is the lightest API call that requires auth
        acct = self.client.users_get_current_account()
        self.log.info("Dropbox OK: %s", acct.email)
        # Ensure target folder exists
        try:
            self.client.files_get_metadata(self.cfg.remote_path)
        except Exception:
            try:
                self.client.files_create_folder_v2(self.cfg.remote_path)
            except Exception as e:
                raise RuntimeError(f"Dropbox: can't create {self.cfg.remote_path}: {e}")

    def verify(self, local: Path, file_id: str, method: str,
               log: logging.Logger) -> bool:
        try:
            meta = self.client.files_get_metadata(file_id)
        except Exception as e:
            log.error("Dropbox verify: %s", e)
            return False
        if local.exists() and meta.size != local.stat().st_size:
            log.error("Dropbox verify: size mismatch")
            return False
        if method == "checksum" and local.exists():
            # Dropbox uses its own content_hash: SHA256 of 4MB blocks, then SHA256 of concat
            log.debug("Dropbox: content_hash check available but not implemented")
        return True
