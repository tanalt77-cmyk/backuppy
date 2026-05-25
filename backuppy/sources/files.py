"""FilesSource: pick up files from one or more paths or glob patterns.

This is the universal source for backuppy. It doesn't care whether the path
is a local folder, an SMB mount, an NFS mount, or a temp dir produced by a
trigger — to backuppy it's all "files on the filesystem".
"""
from __future__ import annotations

import datetime as dt
import fnmatch
import glob
import logging
import re
import shutil
import tarfile
from pathlib import Path


class FilesSource:
    type = "files"

    def __init__(self, cfg: dict, log: logging.Logger):
        self.paths: list[str] = cfg.get("paths", [])
        self.excludes: list[str] = cfg.get("excludes", [])
        self.delete_after_pickup: bool = bool(cfg.get("delete_after_pickup", False))
        # If archive_name is set, all matched files are packed into one tar.
        # Otherwise, each matched file is copied individually.
        self.archive_name: str = cfg.get("archive_name", "") or ""
        # If True, insert a timestamp into the filename when picking up so
        # the uploaded copy is uniquely named even if the source file has
        # a static name. Use with mssql trigger's static_local_name=true.
        # Example: 'work-full.bak' → 'work-full-20260525-143012.bak'
        self.rename_with_timestamp: bool = bool(
            cfg.get("rename_with_timestamp", False)
        )
        self.log = log
        # Remember which files were picked up in the LAST pickup() call,
        # so prefixes() can derive rotation prefixes from real filenames.
        self._last_picked: list[Path] = []

        if not self.paths:
            raise ValueError("FilesSource: 'paths' is required and non-empty")

    def _expand(self) -> list[Path]:
        """Resolve paths and glob patterns to a concrete list of files."""
        out: list[Path] = []
        for pat in self.paths:
            # glob.glob handles both literal paths and patterns; if literal
            # is a directory, we walk it.
            matched = glob.glob(pat, recursive=True)
            for m in matched:
                p = Path(m)
                if p.is_file():
                    if not self._is_excluded(str(p)):
                        out.append(p)
                elif p.is_dir():
                    # Walk directory and pick up all files
                    for f in p.rglob("*"):
                        if f.is_file() and not self._is_excluded(str(f)):
                            out.append(f)
        # Deduplicate while preserving order
        seen: set[Path] = set()
        unique: list[Path] = []
        for p in out:
            r = p.resolve()
            if r not in seen:
                seen.add(r)
                unique.append(p)
        return unique

    def _is_excluded(self, path: str) -> bool:
        for pat in self.excludes:
            if fnmatch.fnmatch(path, pat) or fnmatch.fnmatch("/" + path, pat):
                return True
        return False

    def pickup(self, work_dir: Path, model_name: str) -> list[Path]:
        """Copy/move matched files into work_dir. Returns list of files in work_dir.

        If archive_name is set, pack everything into one tar in work_dir.
        Otherwise, copy each file individually.
        """
        matched = self._expand()
        if not matched:
            self.log.warning("FilesSource: no files matched patterns: %s",
                             self.paths)
            return []

        timestamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        total_size = sum(f.stat().st_size for f in matched)
        self.log.info("FilesSource: %d file(s) matched, %.2f MB total",
                      len(matched), total_size / 1024 / 1024)

        if self.archive_name:
            # Pack into a single tar
            tar_name = f"{model_name}-{self.archive_name}-{timestamp}.tar"
            tar_path = work_dir / tar_name
            self.log.info("  → packing into %s", tar_name)
            with tarfile.open(tar_path, "w") as tar:
                for f in matched:
                    # Preserve a reasonable arcname; use just the basename if
                    # the file came from a glob with mixed parents.
                    arcname = f.name
                    tar.add(f, arcname=arcname)
            produced = [tar_path]
        else:
            # Copy each individually
            produced = []
            for f in matched:
                if self.rename_with_timestamp:
                    # 'work-full.bak' → 'work-full-20260525-143012.bak'
                    stem = f.stem
                    suffix = f.suffix
                    new_name = f"{stem}-{timestamp}{suffix}"
                    dest = work_dir / new_name
                else:
                    dest = work_dir / f.name
                # Handle name collisions (two files with same name from different dirs)
                if dest.exists():
                    base = dest.stem
                    suf = dest.suffix
                    i = 1
                    while (work_dir / f"{base}-{i}{suf}").exists():
                        i += 1
                    dest = work_dir / f"{base}-{i}{suf}"
                shutil.copy2(f, dest)
                produced.append(dest)

        # Delete originals if requested
        if self.delete_after_pickup:
            for f in matched:
                try:
                    f.unlink()
                except OSError as e:
                    self.log.warning("  could not delete %s: %s", f, e)
            self.log.info("  deleted %d source file(s) after pickup", len(matched))

        # Remember produced files for later prefix derivation
        self._last_picked = list(produced)
        return produced

    # Match a timestamp like '-20260524-160013' or '-20260524_160013' at the end
    # of a filename stem. We rotate by everything *before* that timestamp,
    # so daily backups of the same DB ('work-full-...bak') all share a prefix.
    _TIMESTAMP_RE = re.compile(r"[-_]\d{8}[-_]\d{6}.*$")

    def prefixes(self, model_name: str) -> list[str]:
        """Return filename prefixes used by this source for rotation.

        Three modes:
          1. archive_name set → prefix is '<model>-<archive_name>-'
          2. Files have timestamp pattern (e.g. 'work-full-20260524-160013.bak')
             → prefix is the part BEFORE the timestamp ('work-full-')
          3. Otherwise → empty (no rotation, since we can't group runs reliably)
        """
        if self.archive_name:
            return [f"{model_name}-{self.archive_name}-"]

        # Derive prefixes from the files we actually picked up in the last run.
        # Strip the trailing -YYYYMMDD-HHMMSS<ext> part.
        prefixes: set[str] = set()
        for p in self._last_picked:
            name = p.name
            stripped = self._TIMESTAMP_RE.sub("", name)
            if stripped != name and stripped:
                # Keep the part up to and including the last dash, so files like
                #   work-full-20260524-160013.bak  → 'work-full-'
                #   avic-diff-20260524-170015.bak  → 'avic-diff-'
                prefixes.add(stripped + "-")
        return sorted(prefixes)

    def check_access(self) -> None:
        """Preflight: verify at least one path exists or can match something."""
        # We just try expansion — empty result is a warning, not error
        # (some sources may not have files at preflight time but will at run time).
        _ = self._expand()
