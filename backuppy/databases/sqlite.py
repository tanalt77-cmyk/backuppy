"""SQLite dumper using .backup command (safe even while DB is in use)."""
from __future__ import annotations

import datetime as dt
import logging
import shutil
import sqlite3
from pathlib import Path

from ..config import SQLiteCfg
from .base import BaseDumper


class SQLiteDumper(BaseDumper):
    def __init__(self, cfg: SQLiteCfg, log: logging.Logger):
        self.cfg = cfg
        self.log = log

    def _dump_one(self, db_path: str, work_dir: Path, timestamp: str) -> Path:
        src = Path(db_path)
        if not src.exists():
            raise FileNotFoundError(f"SQLite DB not found: {src}")

        dbname = src.stem
        dest = work_dir / f"{dbname}-full-{timestamp}.sqlite"

        if self.cfg.use_online_backup:
            # Online backup — works on live DBs, handles WAL etc.
            self.log.info("SQLite: online backup %s → %s", src.name, dest.name)
            src_conn = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
            dst_conn = sqlite3.connect(dest)
            try:
                src_conn.backup(dst_conn)
            finally:
                dst_conn.close()
                src_conn.close()
        else:
            self.log.info("SQLite: copying %s → %s", src.name, dest.name)
            shutil.copy2(src, dest)

        size_mb = dest.stat().st_size / 1024 / 1024
        self.log.info("  → %s (%.2f MB)", dest.name, size_mb)
        return dest

    def dump_all(self, work_dir: Path) -> list[Path]:
        timestamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        return [self._dump_one(p, work_dir, timestamp)
                for p in self.cfg.databases]

    def check_connection(self) -> None:
        for p in self.cfg.databases:
            path = Path(p)
            if not path.exists():
                raise FileNotFoundError(f"SQLite DB not found: {path}")
            # Quick integrity check
            conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            try:
                result = conn.execute("PRAGMA integrity_check;").fetchone()
                if result[0] != "ok":
                    self.log.warning("SQLite %s: integrity check = %s",
                                     path.name, result[0])
                else:
                    self.log.info("SQLite OK: %s", path.name)
            finally:
                conn.close()

    def prefixes(self) -> list[str]:
        return [f"{Path(p).stem}-full-" for p in self.cfg.databases]
