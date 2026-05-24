"""Google Cloud Storage."""
from __future__ import annotations

import base64
import hashlib
import logging
from pathlib import Path

from ..config import GCSCfg
from .base import BaseStorage


class GCSStorage(BaseStorage):
    name = "gcs"

    def __init__(self, cfg: GCSCfg, log: logging.Logger):
        self.cfg = cfg
        self.log = log
        self._bucket = None

    @property
    def bucket(self):
        if self._bucket is not None:
            return self._bucket
        try:
            from google.cloud import storage
        except ImportError as e:
            raise RuntimeError(
                "google-cloud-storage not installed. pip install google-cloud-storage"
            ) from e

        kwargs = {}
        if self.cfg.project_id:
            kwargs["project"] = self.cfg.project_id
        if self.cfg.credentials_file:
            client = storage.Client.from_service_account_json(
                self.cfg.credentials_file, **kwargs
            )
        else:
            # Falls back to GOOGLE_APPLICATION_CREDENTIALS env or metadata server
            client = storage.Client(**kwargs)
        self._bucket = client.bucket(self.cfg.bucket)
        return self._bucket

    def _key(self, name: str) -> str:
        pref = self.cfg.prefix.strip("/")
        return f"{pref}/{name}" if pref else name

    def upload(self, local: Path) -> str:
        key = self._key(local.name)
        size_mb = local.stat().st_size / 1024 / 1024
        self.log.info("GCS: uploading %s (%.2f MB) → gs://%s/%s",
                      local.name, size_mb, self.cfg.bucket, key)
        blob = self.bucket.blob(key)
        blob.storage_class = self.cfg.storage_class
        blob.upload_from_filename(str(local))
        return f"gs://{self.cfg.bucket}/{key}"

    def list_files(self) -> list[dict]:
        pref = self.cfg.prefix.strip("/")
        prefix_key = pref + "/" if pref else ""
        out = []
        for blob in self.bucket.list_blobs(prefix=prefix_key):
            name = blob.name[len(prefix_key):] if prefix_key else blob.name
            if not name:
                continue
            out.append({
                "name": name, "id": blob.name,
                "size": blob.size or 0,
                "modified": blob.updated,
            })
        return out

    def delete(self, file_id: str) -> None:
        self.bucket.blob(file_id).delete()

    def check_access(self) -> None:
        if not self.bucket.exists():
            raise RuntimeError(f"GCS bucket does not exist: {self.cfg.bucket}")
        self.log.info("GCS OK: bucket=%s", self.cfg.bucket)

    def verify(self, local: Path, file_id: str, method: str,
               log: logging.Logger) -> bool:
        if "/" not in file_id and self.cfg.prefix:
            file_id = self._key(file_id)
        blob = self.bucket.blob(file_id)
        blob.reload()
        if not blob.exists():
            log.error("GCS verify: blob not found")
            return False
        if local.exists() and blob.size != local.stat().st_size:
            log.error("GCS verify: size mismatch")
            return False
        if method == "checksum" and local.exists() and blob.md5_hash:
            local_md5 = hashlib.md5()
            with open(local, "rb") as f:
                for chunk in iter(lambda: f.read(1024 * 1024), b""):
                    local_md5.update(chunk)
            remote_md5_b64 = blob.md5_hash
            local_md5_b64 = base64.b64encode(local_md5.digest()).decode()
            if local_md5_b64 != remote_md5_b64:
                log.error("GCS verify: MD5 mismatch")
                return False
            log.debug("GCS verify: MD5 OK")
        return True
