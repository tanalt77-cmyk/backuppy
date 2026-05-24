"""WebDAV storage (Nextcloud, ownCloud, Hetzner Storage Share).

Supports Nextcloud's chunked upload protocol for large files:
https://docs.nextcloud.com/server/latest/developer_manual/client_apis/WebDAV/chunking.html

Auto-switches: small files use direct PUT, files >= chunked_threshold_mb
use chunked PUT + final MOVE.
"""
from __future__ import annotations

import datetime as dt
import logging
import secrets
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

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

        # For chunked upload we need a separate endpoint:
        # https://<host>/remote.php/dav/uploads/<user>/
        # We derive it from base_url by replacing 'files/<user>' segment.
        # base_url pattern: https://<host>/remote.php/dav/files/<user>/
        self._uploads_base = self._derive_uploads_base()

    def _derive_uploads_base(self) -> str | None:
        """Convert /remote.php/dav/files/<user>/ → /remote.php/dav/uploads/<user>/"""
        url = self.cfg.base_url.rstrip("/")
        if "/dav/files/" in url:
            return url.replace("/dav/files/", "/dav/uploads/") + "/"
        # Not a standard Nextcloud layout → chunked upload won't work
        return None

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

    # ----------------------------------------------------------------------
    # Upload (auto: chunked or direct based on file size)
    # ----------------------------------------------------------------------

    def upload(self, local: Path) -> str:
        size = local.stat().st_size
        size_mb = size / 1024 / 1024
        threshold = self.cfg.chunked_threshold_mb * 1024 * 1024

        use_chunked = (
            self.cfg.chunked
            and self._uploads_base is not None
            and size >= threshold
        )

        if use_chunked:
            self.log.info("WebDAV: chunked upload %s (%.2f MB, %d MB chunks)",
                          local.name, size_mb, self.cfg.chunked_chunk_size_mb)
            return self._upload_chunked(local)
        else:
            if self.cfg.chunked and self._uploads_base is None and size >= threshold:
                self.log.warning(
                    "WebDAV: chunked upload requested but base_url is not a "
                    "standard Nextcloud /remote.php/dav/files/<user>/ — "
                    "falling back to direct PUT (may fail on large files)"
                )
            return self._upload_direct(local)

    # ----------------------------------------------------------------------
    # Direct (single PUT) — for small files
    # ----------------------------------------------------------------------

    def _upload_direct(self, local: Path) -> str:
        from ..progress import Progress, ProgressFile
        self._ensure_dir(self.cfg.remote_path)
        url = self._url(self.cfg.remote_path, local.name)
        size = local.stat().st_size
        size_mb = size / 1024 / 1024
        self.log.info("WebDAV: direct PUT %s (%.2f MB) → %s",
                      local.name, size_mb, url)
        progress = Progress("Uploading (webdav)", total_bytes=size,
                            label=local.name, log=self.log)
        try:
            with open(local, "rb") as f:
                stream = ProgressFile(f, progress)
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

    # ----------------------------------------------------------------------
    # Chunked upload (Nextcloud protocol)
    # ----------------------------------------------------------------------

    def _upload_chunked(self, local: Path) -> str:
        """Three-phase chunked upload:
          1) MKCOL /uploads/<user>/<txid>/
          2) PUT each chunk to that folder as 00000001, 00000002, ...
          3) MOVE /uploads/<user>/<txid>/.file → final destination

        On failure mid-way, the upload folder is left for resume/cleanup —
        Nextcloud auto-cleans abandoned uploads after a while.
        """
        from ..progress import Progress

        self._ensure_dir(self.cfg.remote_path)

        size = local.stat().st_size
        chunk_size = self.cfg.chunked_chunk_size_mb * 1024 * 1024
        total_chunks = (size + chunk_size - 1) // chunk_size

        # Unique transfer ID (avoid collisions if multiple uploads run in parallel)
        txid = f"backuppy-{int(time.time())}-{secrets.token_hex(4)}"
        upload_dir = f"{self._uploads_base.rstrip('/')}/{txid}/"

        # Final destination URL (relative to base files/<user>/)
        final_url = self._url(self.cfg.remote_path, local.name)

        # Phase 1: create the upload folder (MKCOL)
        self.log.debug("WebDAV chunked: MKCOL %s", upload_dir)
        r = requests.request("MKCOL", upload_dir, auth=self.auth,
                             timeout=self.cfg.timeout,
                             verify=self.cfg.verify_tls,
                             headers={"Destination": final_url})
        if r.status_code not in (201, 405):
            raise RuntimeError(
                f"WebDAV chunked: MKCOL {upload_dir} → {r.status_code} "
                f"{r.text[:200]}"
            )

        # Phase 2: upload chunks
        progress = Progress("Uploading (webdav)", total_bytes=size,
                            label=local.name, log=self.log)
        try:
            with open(local, "rb") as f:
                for idx in range(1, total_chunks + 1):
                    chunk_data = f.read(chunk_size)
                    if not chunk_data:
                        break
                    chunk_name = f"{idx:08d}"
                    chunk_url = upload_dir + chunk_name

                    self._put_chunk_with_retry(
                        chunk_url, chunk_data, final_url, idx, total_chunks
                    )
                    progress.advance(len(chunk_data))

            # Phase 3: MOVE the .file pseudo-resource → final location.
            # Nextcloud will assemble all chunks and place them at Destination.
            self.log.debug("WebDAV chunked: MOVE .file → %s", final_url)
            r = requests.request(
                "MOVE",
                upload_dir + ".file",
                auth=self.auth,
                timeout=self.cfg.timeout,
                verify=self.cfg.verify_tls,
                headers={
                    "Destination": final_url,
                    "OC-Total-Length": str(size),
                },
            )
            if r.status_code not in (201, 204):
                progress.done(success=False)
                raise RuntimeError(
                    f"WebDAV chunked: MOVE → {r.status_code} {r.text[:200]}"
                )
            progress.done()
        except Exception:
            progress.done(success=False)
            # Try to clean up the abandoned upload folder
            try:
                requests.delete(upload_dir, auth=self.auth,
                                timeout=self.cfg.timeout,
                                verify=self.cfg.verify_tls)
            except Exception:
                pass
            raise

        return final_url

    def _put_chunk_with_retry(self, chunk_url: str, data: bytes,
                              final_url: str, idx: int, total: int) -> None:
        """PUT a single chunk with retry on transient failures."""
        last_err: Exception | None = None
        for attempt in range(1, self.cfg.chunked_retries + 2):
            try:
                r = requests.put(
                    chunk_url, data=data, auth=self.auth,
                    timeout=self.cfg.timeout,
                    verify=self.cfg.verify_tls,
                    headers={
                        "Content-Length": str(len(data)),
                        "Destination": final_url,
                    },
                )
                if r.status_code in (200, 201, 204):
                    return
                # 5xx and 408/429: retryable
                if r.status_code >= 500 or r.status_code in (408, 429):
                    last_err = RuntimeError(
                        f"chunk {idx}/{total}: HTTP {r.status_code}"
                    )
                else:
                    # 4xx other than retryable: permanent failure
                    raise RuntimeError(
                        f"WebDAV chunked: chunk {idx}/{total} PUT → "
                        f"{r.status_code} {r.text[:200]}"
                    )
            except requests.exceptions.RequestException as e:
                last_err = e

            if attempt <= self.cfg.chunked_retries:
                wait = min(2 ** attempt, 30)
                self.log.warning(
                    "WebDAV chunked: chunk %d/%d attempt %d failed (%s) — "
                    "retrying in %ds",
                    idx, total, attempt, last_err, wait
                )
                time.sleep(wait)

        raise RuntimeError(
            f"WebDAV chunked: chunk {idx}/{total} failed after "
            f"{self.cfg.chunked_retries + 1} attempts: {last_err}"
        )

    # ----------------------------------------------------------------------
    # Listing, delete, verify (unchanged)
    # ----------------------------------------------------------------------

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
                "id": name,
                "size": size,
                "modified": dt.datetime.now(),
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
        if self.cfg.chunked and self._uploads_base is None:
            self.log.warning(
                "WebDAV: chunked=true but base_url doesn't look like Nextcloud "
                "(/remote.php/dav/files/<user>/). Large files will likely fail."
            )
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
        if method == "checksum":
            log.debug("WebDAV: checksum not available, fell back to size")
        return True
