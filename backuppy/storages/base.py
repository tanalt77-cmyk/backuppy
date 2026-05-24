"""Base class for remote storage backends."""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from pathlib import Path


class BaseStorage(ABC):
    """All remote storages implement this contract."""

    name = "base"

    @abstractmethod
    def upload(self, local: Path) -> str:
        """Upload a file. Return a URI/identifier (used for logs)."""

    @abstractmethod
    def list_files(self) -> list[dict]:
        """List backup files. Each dict has at least:
        {'name': str, 'size': int, 'modified': datetime, 'id': str}
        'id' is whatever the storage needs for delete().
        """

    @abstractmethod
    def delete(self, file_id: str) -> None:
        """Delete a file by its storage-specific id."""

    @abstractmethod
    def check_access(self) -> None:
        """Verify connectivity/credentials. Raise on failure."""

    @abstractmethod
    def verify(self, local: Path, file_id: str, method: str,
               log: logging.Logger) -> bool:
        """Verify uploaded file integrity. Returns True if OK.

        method: 'size' or 'checksum'. Backends that can't checksum cheaply
        should fall back to size and log a debug note.
        """

    def rotate(self, name_prefix: str, keep_last: int,
               log: logging.Logger) -> None:
        """Delete files older than keep_last that match prefix."""
        files = [f for f in self.list_files()
                 if f["name"].startswith(name_prefix)]
        files.sort(key=lambda f: f["name"], reverse=True)
        for old in files[keep_last:]:
            log.info("%s: rotating out %s", self.name, old["name"])
            self.delete(old["id"])
