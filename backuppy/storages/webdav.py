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

        # Persistent HTTP session — keeps TCP+TLS connection alive across
        # chunked PUTs, eliminating ~300ms handshake per chunk.
        self._session = requests.Session()
        self._session.auth = self.auth
        # Increase connection pool to handle quick succession of PUTs — and to
        # allow several in-flight at once when chunked_parallel > 1.
        _pool = max(4, getattr(cfg, "chunked_parallel", 1) or 1)
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=_pool,
            pool_maxsize=_pool,
            max_retries=0,            # we handle retries ourselves
        )
        self._session.mount("http://", adapter)
        self._session.mount("https://", adapter)

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

    def _target_dir(self) -> str:
        """The remote directory files actually go into. Includes run-subdir
        if set_run_subdir() was called."""
        if self._run_subdir:
            return f"{self.cfg.remote_path}/{self._run_subdir}"
        return self.cfg.remote_path

    def _ensure_dir(self, remote_dir: str) -> None:
        parts = [p for p in remote_dir.strip("/").split("/") if p]
        for i in range(1, len(parts) + 1):
            url = self._url("/".join(parts[:i])) + "/"
            r = self._session.request("MKCOL", url,
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
        target_dir = self._target_dir()
        self._ensure_dir(target_dir)
        url = self._url(target_dir, local.name)
        size = local.stat().st_size
        size_mb = size / 1024 / 1024
        self.log.info("WebDAV: direct PUT %s (%.2f MB) → %s",
                      local.name, size_mb, url)
        progress = Progress("Uploading (webdav)", total_bytes=size,
                            label=local.name, log=self.log)
        try:
            with open(local, "rb") as f:
                stream = ProgressFile(f, progress)
                r = self._session.put(url, data=stream,
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

        target_dir = self._target_dir()
        self._ensure_dir(target_dir)

        size = local.stat().st_size
        chunk_size = self.cfg.chunked_chunk_size_mb * 1024 * 1024
        total_chunks = (size + chunk_size - 1) // chunk_size

        # Unique transfer ID (avoid collisions if multiple uploads run in parallel)
        txid = f"backuppy-{int(time.time())}-{secrets.token_hex(4)}"
        upload_dir = f"{self._uploads_base.rstrip('/')}/{txid}/"

        # Final destination URL (relative to base files/<user>/)
        final_url = self._url(target_dir, local.name)

        # Phase 1: create the upload folder (MKCOL)
        self.log.debug("WebDAV chunked: MKCOL %s", upload_dir)
        r = self._session.request("MKCOL", upload_dir,
                             timeout=self.cfg.timeout,
                             verify=self.cfg.verify_tls,
                             headers={"Destination": final_url})
        if r.status_code not in (201, 405):
            raise RuntimeError(
                f"WebDAV chunked: MKCOL {upload_dir} → {r.status_code} "
                f"{r.text[:200]}"
            )

        # Phase 2: upload chunks (optionally several at a time)
        progress = Progress("Uploading (webdav)", total_bytes=size,
                            label=local.name, log=self.log)
        parallel = max(1, getattr(self.cfg, "chunked_parallel", 1) or 1)
        try:
            if parallel <= 1:
                with open(local, "rb") as f:
                    for idx in range(1, total_chunks + 1):
                        chunk_data = f.read(chunk_size)
                        if not chunk_data:
                            break
                        self._put_chunk_with_retry(
                            upload_dir + f"{idx:08d}", chunk_data,
                            final_url, idx, total_chunks,
                        )
                        progress.advance(len(chunk_data))
            else:
                self._upload_chunks_parallel(
                    local, upload_dir, final_url, chunk_size,
                    total_chunks, progress, parallel,
                )

            # Phase 3: MOVE the .file pseudo-resource → final location.
            # Nextcloud assembles all chunks server-side; for a large file this
            # can take far longer than the normal request timeout, so use a
            # size-scaled read timeout. If it still times out, the assembly may
            # well have finished anyway — verify the destination before failing.
            self.log.debug("WebDAV chunked: MOVE .file → %s", final_url)
            move_timeout = self._assemble_timeout(size)
            try:
                r = self._session.request(
                    "MOVE",
                    upload_dir + ".file",
                    timeout=move_timeout,
                    verify=self.cfg.verify_tls,
                    headers={
                        "Destination": final_url,
                        "OC-Total-Length": str(size),
                    },
                )
            except requests.exceptions.Timeout:
                self.log.warning(
                    "WebDAV chunked: MOVE timed out after %ss while the server "
                    "assembles %s — checking whether it completed anyway…",
                    move_timeout[1], local.name,
                )
                if self._assembled_ok(final_url, size):
                    self.log.info(
                        "WebDAV chunked: %s assembled OK despite the MOVE "
                        "timeout (data was fully uploaded)", local.name,
                    )
                    progress.done()
                    return final_url
                progress.done(success=False)
                raise
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
                self._session.delete(upload_dir,
                                timeout=self.cfg.timeout,
                                verify=self.cfg.verify_tls)
            except Exception:
                pass
            raise

        return final_url

    def _upload_chunks_parallel(self, local, upload_dir, final_url, chunk_size,
                               total_chunks, progress, parallel):
        """Upload chunks concurrently. Each worker reads its own slice of the
        file (independent handle + seek), so peak memory is bounded by
        chunk_size * parallel rather than the whole file. The first failure is
        re-raised to the caller, which cleans up the upload folder."""
        import concurrent.futures
        import threading

        plock = threading.Lock()

        def _send(idx: int) -> None:
            offset = (idx - 1) * chunk_size
            with open(local, "rb") as fh:
                fh.seek(offset)
                data = fh.read(chunk_size)
            if not data:
                return
            self._put_chunk_with_retry(
                upload_dir + f"{idx:08d}", data, final_url, idx, total_chunks,
            )
            with plock:
                progress.advance(len(data))

        with concurrent.futures.ThreadPoolExecutor(max_workers=parallel) as pool:
            futures = [pool.submit(_send, i) for i in range(1, total_chunks + 1)]
            for fut in concurrent.futures.as_completed(futures):
                fut.result()  # re-raise the first chunk failure

    def _assemble_timeout(self, size: int):
        """(connect, read) timeout for the final MOVE.

        Read timeout scales with file size (≈ a 20 MB/s assembly floor, plus a
        base) so a large file doesn't trip the normal request timeout while the
        server is still concatenating chunks. `chunked_assemble_timeout` > 0
        overrides the read value."""
        override = getattr(self.cfg, "chunked_assemble_timeout", 0) or 0
        if override > 0:
            read = override
        else:
            read = max(self.cfg.timeout, 300 + int(size / (20 * 1024 * 1024)))
        connect = min(self.cfg.timeout, 60)
        return (connect, read)

    def _assembled_ok(self, final_url: str, expected_size: int,
                      tries: int = 30, delay: int = 60) -> bool:
        """After a MOVE timeout, poll the destination: Nextcloud only exposes the
        final path once assembly has finished, so a 200 there (with the expected
        size when the server reports it) means the upload actually succeeded."""
        for _ in range(tries):
            try:
                r = self._session.head(final_url, timeout=self.cfg.timeout,
                                       verify=self.cfg.verify_tls)
                if r.status_code in (200, 204):
                    cl = r.headers.get("Content-Length")
                    if cl is None or int(cl) == expected_size:
                        return True
            except requests.exceptions.RequestException:
                pass
            time.sleep(delay)
        return False

    def _put_chunk_with_retry(self, chunk_url: str, data: bytes,
                              final_url: str, idx: int, total: int) -> None:
        """PUT a single chunk with retry on transient failures."""
        last_err: Exception | None = None
        for attempt in range(1, self.cfg.chunked_retries + 2):
            try:
                r = self._session.put(
                    chunk_url, data=data,
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
        r = self._session.request("PROPFIND", url, data=body,
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

    def list_run_dirs(self) -> list[str]:
        """List run-subdirectories under remote_path. PROPFIND Depth: 1
        gives us the immediate children — filter to those that are collections.
        """
        url = self._url(self.cfg.remote_path) + "/"
        body = (
            '<?xml version="1.0"?>'
            '<d:propfind xmlns:d="DAV:"><d:prop>'
            '<d:resourcetype/><d:displayname/>'
            '</d:prop></d:propfind>'
        )
        r = self._session.request(
            "PROPFIND", url, data=body,
            headers={"Depth": "1", "Content-Type": "application/xml"},
            timeout=self.cfg.timeout,
            verify=self.cfg.verify_tls,
        )
        if r.status_code not in (207, 200):
            raise RuntimeError(f"PROPFIND {url} → {r.status_code}")

        ns = {"d": "DAV:"}
        root = ET.fromstring(r.text)
        dirs: list[str] = []
        for resp in root.findall("d:response", ns):
            href = resp.find("d:href", ns)
            if href is None or href.text is None:
                continue
            stripped = href.text.rstrip("/")
            name = unquote(stripped.rsplit("/", 1)[-1])
            if not name or stripped.endswith(self.cfg.remote_path.strip("/")):
                continue
            # Check if it's a collection (directory)
            rt = resp.find(".//d:resourcetype", ns)
            if rt is not None and rt.find("d:collection", ns) is not None:
                dirs.append(name)
        return sorted(dirs)

    def delete_run_dir(self, dir_name: str, log: logging.Logger) -> None:
        """Delete an entire directory with one DELETE request — Nextcloud
        deletes the folder and all its contents.
        """
        url = self._url(self.cfg.remote_path, dir_name) + "/"
        r = self._session.delete(url,
                            timeout=self.cfg.timeout,
                            verify=self.cfg.verify_tls)
        if r.status_code not in (200, 204, 404):
            raise RuntimeError(f"DELETE {url} → {r.status_code}")
        log.debug("WebDAV: deleted directory %s", dir_name)

    def delete(self, file_id: str) -> None:
        # file_id may already contain run-subdir/path, or be just a basename;
        # if it has '/', treat as relative-to-remote_path
        if "/" in file_id:
            url = self._url(self.cfg.remote_path, file_id)
        else:
            url = self._url(self._target_dir(), file_id)
        r = self._session.delete(url,
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
        url = self._url(self._target_dir(), file_id)
        r = self._session.head(url, timeout=self.cfg.timeout,
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
