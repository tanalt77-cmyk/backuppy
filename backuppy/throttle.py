"""Bandwidth throttling for uploads.

Wraps a file-like object so reads are rate-limited. Works with any storage
that reads the file linearly (S3 multipart, WebDAV PUT, SFTP, etc.).
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import IO


class ThrottledReader:
    """Wraps a binary file object, sleeping between reads to enforce rate."""

    def __init__(self, fileobj: IO[bytes], bytes_per_sec: int):
        self._f = fileobj
        self._rate = max(1, bytes_per_sec)
        self._start = time.monotonic()
        self._bytes_read = 0

    def read(self, size: int = -1) -> bytes:
        data = self._f.read(size)
        if not data:
            return data
        self._bytes_read += len(data)
        elapsed = time.monotonic() - self._start
        expected = self._bytes_read / self._rate
        if expected > elapsed:
            time.sleep(expected - elapsed)
        return data

    # Methods storage libraries might call
    def __iter__(self):
        return self

    def __next__(self):
        chunk = self.read(64 * 1024)
        if not chunk:
            raise StopIteration
        return chunk

    def __getattr__(self, name):
        return getattr(self._f, name)


def throttled_open(path: Path, kbps: int):
    """Context manager helper. Use:
        with throttled_open(path, 1000) as f:
            requests.put(url, data=f)
    """
    f = open(path, "rb")
    if kbps <= 0:
        return f
    return ThrottledReader(f, kbps * 1024)
