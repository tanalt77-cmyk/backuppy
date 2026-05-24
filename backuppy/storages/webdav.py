"""WebDAV storage (Nextcloud, ownCloud, Hetzner Storage Share)."""
from __future__ import annotations

import datetime as dt
import hashlib
import logging
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import quote, unquote

import requests
from requests.auth import HTTPBasicAuth

from ..config import WebDAVCfg
from .base import BaseStorage


class WebDAVStorage(BaseStorage):
    name = "webdav"

    def __init__(self, cfg: WebDAVCfg, log: logging.Logger):
        self.cfg = cfg
        self.log = log
        self.auth = HTTPBasicAuth(cfg.username, cfg.password)
        self.base = cfg.base_url.rstrip("/") + "/"

    def _url(self, *parts: str) -> str:
        path = "/".join(quote(p.strip("/"), safe="/") for p in parts if p)
        return self.base + path

    def _ensure_dir(self, remote_dir: str) -> None:
        parts = [p for p in remote_dir.strip("/").split("/") if p]
        for i in range(1, len(parts) + 1):
            url = self._url("/".join(parts[:i])) + "/"
            r = requests.request("MKCOL", url, auth=self.auth,
                                 timeout=self.cfg.timeout,
                                 verify=self.cfg.verify_tls)
            if r.status_code not in (201, 405, 301):
                raise RuntimeError(f"MKCOL {url} → {r.status_code} {r.text[:200]}")

    def upload(self, local: Path) -> str:
        from ..progress import Progress, ProgressFile
        self._ensure_dir(self.cfg.remote_path)
        url = self._url(self.cfg.remote_path, local.name)
        size = local.stat().st_size
        size_mb = size / 1024 / 1024
        self.log.info("WebDAV: uploading %s (%.2f MB) → %s",
                      local.name, size_mb, url)
        progress = Progress("Uploading (webdav)", total_bytes=size,
                            label=local.name, log=self.log)
        try:
            with open(local, "rb") as f:
                stream = ProgressFile(f, progress)
                # requests-PUT chunked: we send the wrapped stream as data;
                # requests pulls .read() iteratively, which advances progress.
                r = requests.put(url, data=stream, auth=self.auth,
                                 timeout=self.cfg.timeout,
                                 verify=self.cfg.verify_tls,
                                 headers={"Content-Length": str(size)})
            if r.status_code not in (200, 201, 204):
                progress.done(success=False)
                raise RuntimeError(f"PUT {url} → {r.status_code} {r.text[:200]}")
            progress.done()
        except Exception:
            progress.done(success=False)
            raise
        return url

    def list_files(self) -> list[dict]:
        url = self._url(self.cfg.remote_path) + "/"
        body = (
            '<?xml version="1.0"?>'
            '<d:propfind xmlns:d="DAV:"><d:prop>'
            '<d:displayname/><d:getcontentlength/><d:getlastmodified/>'
            '</d:prop></d:propfind>'
        )
        r = requests.request("PROPFIND", url, data=body, auth=self.auth,
                             headers={"Depth": "1",
                                      "Content-Type": "application/xml"},
                             timeout=self.cfg.timeout,
                             verify=self.cfg.verify_tls)
        if r.status_code not in (207, 200):
            raise RuntimeError(f"PROPFIND {url} → {r.status_code}")

        ns = {"d": "DAV:"}
        root = ET.fromstring(r.text)
        out: list[dict] = []
        for resp in root.findall("d:response", ns):
            href = resp.find("d:href", ns)
            if href is None or href.text is None:
                continue
            stripped = href.text.rstrip("/")
            name = unquote(stripped.rsplit("/", 1)[-1])
            if not name or stripped.endswith(self.cfg.remote_path.strip("/")):
                continue
            size_el = resp.find(".//d:getcontentlength", ns)
            size = int(size_el.text) if size_el is not None and size_el.text else 0
            out.append({
                "name": name,
                "id": name,  # we re-build URL on delete
                "size": size,
                "modified": dt.datetime.now(),  # parsing webdav dates is finicky; skip
            })
        return out

    def delete(self, file_id: str) -> None:
        url = self._url(self.cfg.remote_path, file_id)
        r = requests.delete(url, auth=self.auth,
                            timeout=self.cfg.timeout,
                            verify=self.cfg.verify_tls)
        if r.status_code not in (200, 204, 404):
            raise RuntimeError(f"DELETE {url} → {r.status_code}")

    def check_access(self) -> None:
        self._ensure_dir(self.cfg.remote_path)
        self.log.info("WebDAV OK: %s%s", self.cfg.base_url, self.cfg.remote_path)

    def verify(self, local: Path, file_id: str, method: str,
               log: logging.Logger) -> bool:
        url = self._url(self.cfg.remote_path, file_id)
        r = requests.head(url, auth=self.auth, timeout=self.cfg.timeout,
                          verify=self.cfg.verify_tls)
        if r.status_code != 200:
            log.error("WebDAV verify: HEAD %s → %s", url, r.status_code)
            return False
        remote_size = int(r.headers.get("Content-Length", -1))
        local_size = local.stat().st_size if local.exists() else None
        if local_size is not None and remote_size != local_size:
            log.error("WebDAV verify: size mismatch %s vs %s",
                      remote_size, local_size)
            return False
        # No standard checksum header in WebDAV; size is best we have
        if method == "checksum":
            log.debug("WebDAV: checksum not available, fell back to size")
        return True
