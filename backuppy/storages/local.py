"""Local filesystem 'storage'. Always enabled."""
from __future__ import annotations

import datetime as dt
import hashlib
import logging
import shutil
from pathlib import Path

from ..config import LocalCfg
from .base import BaseStorage


class LocalStorage(BaseStorage):
    name = "local"

    def __init__(self, cfg: LocalCfg, log: logging.Logger):
        self.cfg = cfg
        self.log = log
        self.dest_dir = Path(cfg.path)
        self.dest_dir.mkdir(parents=True, exist_ok=True)

    def store(self, src: Path) -> Path:
        """Move src into the local backup directory."""
        dest = self.dest_dir / src.name
        shutil.move(str(src), dest)
        self.log.info("Local: stored %s", dest)
        return dest

    def upload(self, local: Path) -> str:
        # Local 'upload' = move (already done via store(); this is a no-op variant).
        return str(local)

    def list_files(self) -> list[dict]:
        out = []
        if not self.dest_dir.exists():
            return out
        for p in self.dest_dir.iterdir():
            if p.is_file():
                st = p.stat()
                out.append({
                    "name": p.name,
                    "id": str(p),
                    "size": st.st_size,
                    "modified": dt.datetime.fromtimestamp(st.st_mtime),
                })
        return out

    def delete(self, file_id: str) -> None:
        p = Path(file_id)
        if p.exists():
            p.unlink()

    def check_access(self) -> None:
        if not self.dest_dir.is_dir():
            raise RuntimeError(f"local.path is not a directory: {self.dest_dir}")
        # try write+delete
        probe = self.dest_dir / ".backuppy-probe"
        probe.write_text("ok")
        probe.unlink()
        self.log.info("Local OK: %s", self.dest_dir)

    def verify(self, local: Path, file_id: str, method: str,
               log: logging.Logger) -> bool:
        # Local 'upload' is just a move; verify against the moved file.
        p = Path(file_id)
        if not p.exists():
            log.error("local verify: missing %s", p)
            return False
        if p.stat().st_size != local.stat().st_size and local.exists():
            log.error("local verify: size mismatch")
            return False
        return True


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(chunk_size):
            h.update(chunk)
    return h.hexdigest()
