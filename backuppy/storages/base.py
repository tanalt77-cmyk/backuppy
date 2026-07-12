"""Base class for remote storage backends."""
from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod
from pathlib import Path

# Run subdirectories are named strftime("%Y%m%d-%H%M%S"). Rotation must only
# ever consider directories matching this shape — stray folders left by other
# tooling (or older layouts) must never be counted as run dirs, otherwise a
# lexicographic sort can push the *current* run dir past keep_last and delete
# the archive we just wrote.
RUN_DIR_RE = re.compile(r"^\d{8}-\d{6}$")


class BaseStorage(ABC):
    """All remote storages implement this contract."""

    name = "base"

    # When set (via set_run_subdir), every upload goes into <remote_path>/<run_subdir>/
    # and rotation works per-directory instead of per-filename-prefix.
    _run_subdir: str | None = None

    def set_run_subdir(self, subdir: str | None) -> None:
        """Tell this storage to scope its operations to a sub-directory
        (e.g. '20260525-021713'). Use None to clear.

        Storages that support it should:
        - Create the subdir on first upload
        - Place all upload()/verify() in <remote_path>/<subdir>/
        - In list_dirs() return run-subdirs; in delete_dir(name) remove one"""
        self._run_subdir = subdir

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
        """Delete files older than keep_last that match prefix.

        Per-file rotation, used when group_by_run is False.
        """
        files = [f for f in self.list_files()
                 if f["name"].startswith(name_prefix)]
        files.sort(key=lambda f: f["name"], reverse=True)
        for old in files[keep_last:]:
            log.info("%s: rotating out %s", self.name, old["name"])
            self.delete(old["id"])

    # ----- Run-directory mode (group_by_run = True) -----
    # Default implementations delegate to per-file ops; storages can override
    # if they have a more efficient way (e.g. WebDAV deleting a whole folder
    # with one DELETE).

    def list_run_dirs(self) -> list[str]:
        """Return a list of run-subdirectory names that this storage knows
        about. Default: derive from list_files() by looking for top-level
        directory components. Override for efficiency.
        """
        seen: set[str] = set()
        for f in self.list_files():
            name = f["name"]
            if "/" in name:
                top = name.split("/", 1)[0]
                if RUN_DIR_RE.match(top):
                    seen.add(top)
        return sorted(seen)

    def delete_run_dir(self, dir_name: str, log: logging.Logger) -> None:
        """Delete an entire run-subdirectory and all files inside it.
        Default: list files starting with dir_name+'/', delete each.
        """
        for f in self.list_files():
            if f["name"].startswith(dir_name + "/"):
                log.info("%s: deleting %s", self.name, f["name"])
                self.delete(f["id"])

    def rotate_run_dirs(self, keep_last: int, log: logging.Logger) -> None:
        """Per-directory rotation. Keep N most recent run subdirectories."""
        dirs = sorted(self.list_run_dirs(), reverse=True)
        for old in dirs[keep_last:]:
            log.info("%s: rotating out directory %s", self.name, old)
            self.delete_run_dir(old, log)
