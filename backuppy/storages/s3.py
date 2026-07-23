"""Amazon S3 and S3-compatible storage."""
from __future__ import annotations

import datetime as dt
import hashlib
import logging
import threading
import time
from pathlib import Path

from ..config import S3Cfg
from .base import BaseStorage


class _RetryLogHandler(logging.Handler):
    """Surface boto3's retry activity in the engine log — WITHOUT flooding it.

    boto3 retries transient S3 errors (InternalError, ServiceUnavailable,
    SlowDown, 500/503) silently, so a struggling upload used to look like it
    "failed without retrying". Logging every single retry, however, buries the
    real log: a busy upload emits hundreds of lines, which pushes the
    "=== Done ===" marker out of the log tail the portal reads and makes healthy
    models show up as "never ran". So retries are COUNTED and reported at most
    once per _RETRY_LOG_EVERY seconds, plus a total at the end of each upload.
    """

    def __init__(self, target: logging.Logger):
        super().__init__(level=logging.DEBUG)
        self._t = target

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = record.getMessage()
        except Exception:  # noqa: BLE001
            return
        low = msg.lower().lstrip()
        # A real retry line STARTS WITH "retry needed" (both standard and legacy
        # modes). Matching the start avoids the "No retry needed." /
        # "Not retrying request." false positives that a substring match hits.
        if not (low.startswith("retry needed") or "quota reached" in low):
            return
        with _RETRY_STATS["lock"]:
            _RETRY_STATS["total"] += 1
            n = _RETRY_STATS["total"]
            now = time.monotonic()
            due = now - _RETRY_STATS["last_log"] >= _RETRY_LOG_EVERY
            if n == 1 or due:
                _RETRY_STATS["last_log"] = now
                shown = n - _RETRY_STATS["last_reported"]
                _RETRY_STATS["last_reported"] = n
                self._t.warning(
                    "S3 retry: %d transient error(s) retried (total %d this upload) "
                    "— B2 throttling/flakiness, upload continues", shown, n,
                )


# Retry bookkeeping: counted here, summarised at most once per interval.
_RETRY_LOG_EVERY = 60          # seconds between retry summary lines
_RETRY_STATS = {
    "lock": threading.Lock(),
    "total": 0,
    "last_log": 0.0,
    "last_reported": 0,
}


def _retry_stats_reset() -> None:
    with _RETRY_STATS["lock"]:
        _RETRY_STATS["total"] = 0
        _RETRY_STATS["last_log"] = 0.0
        _RETRY_STATS["last_reported"] = 0


def _retry_stats_total() -> int:
    with _RETRY_STATS["lock"]:
        return _RETRY_STATS["total"]


_RETRY_LOG_INSTALLED = False


def _install_retry_logging(target: logging.Logger) -> None:
    """Attach the retry-logging handler to botocore's retry loggers (once)."""
    global _RETRY_LOG_INSTALLED
    if _RETRY_LOG_INSTALLED:
        return
    handler = _RetryLogHandler(target)
    for name in (
        "botocore.retryhandler",
        "botocore.retries.standard",
        "botocore.retries.adaptive",
    ):
        lg = logging.getLogger(name)
        lg.setLevel(logging.DEBUG)
        lg.addHandler(handler)
        lg.propagate = False  # keep DEBUG out of the root logger; we capture it here
    _RETRY_LOG_INSTALLED = True


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
                retries={"max_attempts": self.cfg.max_retries, "mode": "adaptive"},
                signature_version="s3v4",
            ),
        }
        if self.cfg.access_key_id and self.cfg.secret_access_key:
            kwargs["aws_access_key_id"] = self.cfg.access_key_id
            kwargs["aws_secret_access_key"] = self.cfg.secret_access_key
        if self.cfg.endpoint_url:
            endpoint = self.cfg.endpoint_url.strip()
            # Auto-add https:// if user wrote just 'host.example.com'
            if not endpoint.startswith(("http://", "https://")):
                endpoint = "https://" + endpoint
                self.log.debug("S3: normalized endpoint to %s", endpoint)
            kwargs["endpoint_url"] = endpoint

        _install_retry_logging(self.log)
        self._client = boto3.client("s3", **kwargs)
        return self._client

    def _key(self, name: str) -> str:
        # Compose the S3 key: <prefix>/<run_subdir>/<name>, omitting empty
        # segments. _run_subdir is set by core when group_by_run is on; if
        # we ignore it, every artifact lands flat in the bucket, list_files
        # sees no '/' in names, list_run_dirs returns empty, and rotation
        # silently keeps EVERY backup forever — observed 14 files when
        # keep_last=7 was configured.
        parts = [p for p in (self.cfg.prefix.strip("/"), self._run_subdir, name) if p]
        return "/".join(parts)

    def upload(self, local: Path) -> str:
        from boto3.s3.transfer import TransferConfig
        from ..progress import Progress, progress_callback

        key = self._key(local.name)
        size = local.stat().st_size
        size_mb = size / 1024 / 1024
        self.log.info("S3: uploading %s (%.2f MB) → s3://%s/%s",
                      local.name, size_mb, self.cfg.bucket, key)

        extra: dict = {"StorageClass": self.cfg.storage_class}
        if self.cfg.server_side_encryption:
            extra["ServerSideEncryption"] = self.cfg.server_side_encryption

        transfer = TransferConfig(
            multipart_threshold=self.cfg.multipart_threshold_mb * 1024 * 1024,
            multipart_chunksize=self.cfg.multipart_chunksize_mb * 1024 * 1024,
            max_concurrency=max(1, self.cfg.max_concurrency),
            use_threads=True,
        )

        progress = Progress("Uploading (s3)", total_bytes=size,
                            label=local.name, log=self.log)
        _retry_stats_reset()
        try:
            self.client.upload_file(
                Filename=str(local), Bucket=self.cfg.bucket, Key=key,
                ExtraArgs=extra, Config=transfer,
                Callback=progress_callback(progress),
            )
            progress.done()
        except Exception:
            progress.done(success=False)
            raise
        finally:
            # One summary line per upload instead of one line per retry.
            retries = _retry_stats_total()
            if retries:
                self.log.warning(
                    "S3: %d transient error(s) retried during this upload", retries)
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
        # On a versioned bucket (Backblaze B2 keeps every version), a plain
        # delete_object only writes a *delete-marker*: the data survives as a
        # hidden version and keeps occupying space, so rotation never actually
        # frees anything. To reclaim space we remove EVERY version — and any
        # delete-markers — of this exact key.
        to_delete = []
        try:
            paginator = self.client.get_paginator("list_object_versions")
            for page in paginator.paginate(Bucket=self.cfg.bucket, Prefix=file_id):
                for v in page.get("Versions", []):
                    if v.get("Key") == file_id:
                        to_delete.append({"Key": v["Key"], "VersionId": v["VersionId"]})
                for m in page.get("DeleteMarkers", []):
                    if m.get("Key") == file_id:
                        to_delete.append({"Key": m["Key"], "VersionId": m["VersionId"]})
        except Exception as e:  # noqa: BLE001
            self.log.debug("S3: version listing failed for %s (%s); plain delete", file_id, e)
            self.client.delete_object(Bucket=self.cfg.bucket, Key=file_id)
            return

        if not to_delete:
            self.client.delete_object(Bucket=self.cfg.bucket, Key=file_id)
            return

        for i in range(0, len(to_delete), 1000):
            self.client.delete_objects(
                Bucket=self.cfg.bucket,
                Delete={"Objects": to_delete[i:i + 1000], "Quiet": True},
            )

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
