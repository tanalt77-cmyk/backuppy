"""Azure Blob storage."""
from __future__ import annotations

import base64
import hashlib
import logging
from pathlib import Path

from ..config import AzureBlobCfg
from .base import BaseStorage


class AzureBlobStorage(BaseStorage):
    name = "azure"

    def __init__(self, cfg: AzureBlobCfg, log: logging.Logger):
        self.cfg = cfg
        self.log = log
        self._client = None

    @property
    def client(self):
        if self._client is not None:
            return self._client
        try:
            from azure.storage.blob import BlobServiceClient
        except ImportError as e:
            raise RuntimeError(
                "azure-storage-blob not installed. pip install azure-storage-blob"
            ) from e

        if self.cfg.connection_string:
            svc = BlobServiceClient.from_connection_string(self.cfg.connection_string)
        elif self.cfg.account_name and self.cfg.account_key:
            account_url = f"https://{self.cfg.account_name}.blob.core.windows.net"
            svc = BlobServiceClient(account_url=account_url,
                                    credential=self.cfg.account_key)
        else:
            raise RuntimeError(
                "Azure needs either connection_string OR (account_name + account_key)"
            )
        self._client = svc.get_container_client(self.cfg.container)
        return self._client

    def _key(self, name: str) -> str:
        pref = self.cfg.prefix.strip("/")
        return f"{pref}/{name}" if pref else name

    def upload(self, local: Path) -> str:
        from azure.storage.blob import StandardBlobTier

        key = self._key(local.name)
        size_mb = local.stat().st_size / 1024 / 1024
        self.log.info("Azure: uploading %s (%.2f MB) → %s/%s",
                      local.name, size_mb, self.cfg.container, key)

        tier = StandardBlobTier(self.cfg.tier)
        with open(local, "rb") as f:
            self.client.upload_blob(name=key, data=f, overwrite=True,
                                    standard_blob_tier=tier)
        return f"azure://{self.cfg.container}/{key}"

    def list_files(self) -> list[dict]:
        pref = self.cfg.prefix.strip("/")
        prefix_key = pref + "/" if pref else ""
        out = []
        for blob in self.client.list_blobs(name_starts_with=prefix_key):
            name = blob.name[len(prefix_key):] if prefix_key else blob.name
            if not name:
                continue
            out.append({
                "name": name, "id": blob.name,
                "size": blob.size or 0,
                "modified": blob.last_modified,
            })
        return out

    def delete(self, file_id: str) -> None:
        self.client.delete_blob(file_id)

    def check_access(self) -> None:
        props = self.client.get_container_properties()
        self.log.info("Azure OK: container=%s", props.name)

    def verify(self, local: Path, file_id: str, method: str,
               log: logging.Logger) -> bool:
        if "/" not in file_id and self.cfg.prefix:
            file_id = self._key(file_id)
        blob = self.client.get_blob_client(file_id)
        try:
            props = blob.get_blob_properties()
        except Exception as e:
            log.error("Azure verify: %s", e)
            return False
        if local.exists() and props.size != local.stat().st_size:
            log.error("Azure verify: size mismatch")
            return False
        if method == "checksum" and local.exists() and props.content_settings.content_md5:
            local_md5 = hashlib.md5()
            with open(local, "rb") as f:
                for chunk in iter(lambda: f.read(1024 * 1024), b""):
                    local_md5.update(chunk)
            if local_md5.digest() != props.content_settings.content_md5:
                log.error("Azure verify: MD5 mismatch")
                return False
            log.debug("Azure verify: MD5 OK")
        return True
