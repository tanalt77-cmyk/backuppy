"""SFTP storage using paramiko."""
from __future__ import annotations

import datetime as dt
import hashlib
import logging
import posixpath
from pathlib import Path

from ..config import SFTPCfg
from .base import BaseStorage


class SFTPStorage(BaseStorage):
    name = "sftp"

    def __init__(self, cfg: SFTPCfg, log: logging.Logger):
        self.cfg = cfg
        self.log = log

    def _connect(self):
        try:
            import paramiko
        except ImportError as e:
            raise RuntimeError("paramiko not installed. pip install paramiko") from e

        client = paramiko.SSHClient()
        if self.cfg.known_hosts:
            client.load_host_keys(self.cfg.known_hosts)
            client.set_missing_host_key_policy(paramiko.RejectPolicy())
        else:
            self.log.warning(
                "SFTP: known_hosts not set — accepting any host key (insecure)"
            )
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        kwargs = {"hostname": self.cfg.host, "port": self.cfg.port,
                  "username": self.cfg.username, "timeout": 30}
        if self.cfg.key_file:
            kwargs["key_filename"] = self.cfg.key_file
            if self.cfg.key_passphrase:
                kwargs["passphrase"] = self.cfg.key_passphrase
        elif self.cfg.password:
            kwargs["password"] = self.cfg.password
        else:
            raise RuntimeError("SFTP needs either password or key_file")

        client.connect(**kwargs)
        return client, client.open_sftp()

    def _ensure_dir(self, sftp, remote_dir: str) -> None:
        parts = [p for p in remote_dir.strip("/").split("/") if p]
        cur = ""
        for p in parts:
            cur = posixpath.join(cur, p) if cur else p
            try:
                sftp.stat(cur if cur.startswith("/") else "/" + cur if remote_dir.startswith("/") else cur)
            except FileNotFoundError:
                target = cur if not remote_dir.startswith("/") else "/" + cur
                sftp.mkdir(target)

    def _remote(self, *parts: str) -> str:
        return posixpath.join(self.cfg.remote_path, *parts)

    def upload(self, local: Path) -> str:
        client, sftp = self._connect()
        try:
            self._ensure_dir(sftp, self.cfg.remote_path)
            remote_path = self._remote(local.name)
            size_mb = local.stat().st_size / 1024 / 1024
            self.log.info("SFTP: uploading %s (%.2f MB) → %s@%s:%s",
                          local.name, size_mb,
                          self.cfg.username, self.cfg.host, remote_path)
            sftp.put(str(local), remote_path)
            return f"sftp://{self.cfg.username}@{self.cfg.host}{remote_path}"
        finally:
            sftp.close()
            client.close()

    def list_files(self) -> list[dict]:
        client, sftp = self._connect()
        try:
            try:
                entries = sftp.listdir_attr(self.cfg.remote_path)
            except FileNotFoundError:
                return []
            out = []
            for e in entries:
                if e.filename in (".", ".."):
                    continue
                out.append({
                    "name": e.filename,
                    "id": e.filename,
                    "size": e.st_size or 0,
                    "modified": dt.datetime.fromtimestamp(e.st_mtime or 0),
                })
            return out
        finally:
            sftp.close()
            client.close()

    def delete(self, file_id: str) -> None:
        client, sftp = self._connect()
        try:
            sftp.remove(self._remote(file_id))
        finally:
            sftp.close()
            client.close()

    def check_access(self) -> None:
        client, sftp = self._connect()
        try:
            self._ensure_dir(sftp, self.cfg.remote_path)
            self.log.info("SFTP OK: %s@%s:%s",
                          self.cfg.username, self.cfg.host, self.cfg.remote_path)
        finally:
            sftp.close()
            client.close()

    def verify(self, local: Path, file_id: str, method: str,
               log: logging.Logger) -> bool:
        client, sftp = self._connect()
        try:
            remote_path = self._remote(file_id)
            try:
                attrs = sftp.stat(remote_path)
            except FileNotFoundError:
                log.error("SFTP verify: %s not found", remote_path)
                return False
            local_size = local.stat().st_size if local.exists() else None
            if local_size is not None and attrs.st_size != local_size:
                log.error("SFTP verify: size mismatch")
                return False
            # SFTP doesn't expose checksums standard-way; size only
            if method == "checksum":
                log.debug("SFTP: checksum not available, size-only check")
            return True
        finally:
            sftp.close()
            client.close()
