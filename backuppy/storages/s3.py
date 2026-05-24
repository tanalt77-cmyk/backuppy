"""Amazon S3 and S3-compatible storage."""
from __future__ import annotations

import datetime as dt
import hashlib
import logging
from pathlib import Path

from ..config import S3Cfg
from .base import BaseStorage


class S3Storage(BaseStorage):
    name = "s3"

    def __init__(self, cfg: S3Cfg, log: logging.Logger):
        self.cfg = cfg
        self.log = log
        self._client = None

    @property
    def client(self):
        if self._client is not None:
            return self._client
        try:
            import boto3
            from botocore.config import Config as BotoConfig
        except ImportError as e:
            raise RuntimeError("boto3 not installed. pip install boto3") from e

        kwargs = {
            "region_name": self.cfg.region,
            "config": BotoConfig(
                retries={"max_attempts": 5, "mode": "standard"},
                signature_version="s3v4",
            ),
        }
        if self.cfg.access_key_id and self.cfg.secret_access_key:
            kwargs["aws_access_key_id"] = self.cfg.access_key_id
            kwargs["aws_secret_access_key"] = self.cfg.secret_access_key
        if self.cfg.endpoint_url:
            kwargs["endpoint_url"] = self.cfg.endpoint_url

        self._client = boto3.client("s3", **kwargs)
        return self._client

    def _key(self, name: str) -> str:
        pref = self.cfg.prefix.strip("/")
        return f"{pref}/{name}" if pref else name

    def upload(self, local: Path) -> str:
        from boto3.s3.transfer import TransferConfig

        key = self._key(local.name)
        size_mb = local.stat().st_size / 1024 / 1024
        self.log.info("S3: uploading %s (%.2f MB) → s3://%s/%s",
                      local.name, size_mb, self.cfg.bucket, key)

        extra: dict = {"StorageClass": self.cfg.storage_class}
        if self.cfg.server_side_encryption:
            extra["ServerSideEncryption"] = self.cfg.server_side_encryption

        transfer = TransferConfig(
            multipart_threshold=self.cfg.multipart_threshold_mb * 1024 * 1024,
            multipart_chunksize=self.cfg.multipart_chunksize_mb * 1024 * 1024,
            use_threads=True,
        )

        self.client.upload_file(
            Filename=str(local), Bucket=self.cfg.bucket, Key=key,
            ExtraArgs=extra, Config=transfer,
        )
        return f"s3://{self.cfg.bucket}/{key}"

    def list_files(self) -> list[dict]:
        pref = self.cfg.prefix.strip("/")
        prefix_key = pref + "/" if pref else ""
        out: list[dict] = []
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.cfg.bucket, Prefix=prefix_key):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                name = key[len(prefix_key):] if prefix_key else key
                if not name:
                    continue
                out.append({
                    "name": name, "id": key,
                    "size": obj["Size"],
                    "modified": obj["LastModified"],
                })
        return out

    def delete(self, file_id: str) -> None:
        self.client.delete_object(Bucket=self.cfg.bucket, Key=file_id)

    def check_access(self) -> None:
        self.client.head_bucket(Bucket=self.cfg.bucket)
        self.log.info("S3 OK: bucket=%s", self.cfg.bucket)

    def verify(self, local: Path, file_id: str, method: str,
               log: logging.Logger) -> bool:
        # file_id may be just the name; if so, convert to full key
        if "/" not in file_id and self.cfg.prefix:
            file_id = self._key(file_id)
        try:
            head = self.client.head_object(Bucket=self.cfg.bucket, Key=file_id)
        except Exception as e:
            log.error("S3 verify: HEAD failed: %s", e)
            return False

        remote_size = head["ContentLength"]
        local_size = local.stat().st_size if local.exists() else None
        if local_size is not None and remote_size != local_size:
            log.error("S3 verify: size mismatch (remote=%d, local=%d)",
                      remote_size, local_size)
            return False

        if method == "checksum" and local.exists():
            etag = head.get("ETag", "").strip('"')
            # ETag is MD5 only for non-multipart uploads. For multipart it's
            # 'md5-of-parts-md5-N'. Skip in that case.
            if "-" not in etag and len(etag) == 32:
                local_md5 = hashlib.md5()
                with open(local, "rb") as f:
                    for chunk in iter(lambda: f.read(1024 * 1024), b""):
                        local_md5.update(chunk)
                if local_md5.hexdigest() != etag:
                    log.error("S3 verify: ETag/MD5 mismatch")
                    return False
                log.debug("S3 verify: MD5 OK")
            else:
                log.debug("S3 verify: multipart ETag, size-only check")
        return True
