"""SQLite trigger: online .backup of one or more DB files into output_dir."""
from __future__ import annotations

import datetime as dt
import logging
import shutil
import sqlite3
from pathlib import Path

from .base import BaseTrigger


class SQLiteTrigger(BaseTrigger):
    type = "sqlite"

    def __init__(self, cfg: dict, log: logging.Logger):
        super().__init__(cfg, log)
        self.output_dir: str = cfg["output_dir"]
        self.databases: list[str] = cfg["databases"]
        if not self.databases:
            raise ValueError("sqlite trigger: 'databases' is required")
        self.use_online_backup: bool = bool(cfg.get("use_online_backup", True))

    def run(self) -> list[str]:
        out_dir = Path(self.output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        timestamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        produced: list[str] = []

        for p in self.databases:
            src = Path(p)
            if not src.exists():
                raise FileNotFoundError(f"SQLite DB not found: {src}")
            dest = out_dir / f"{src.stem}-full-{timestamp}.sqlite"

            if self.use_online_backup:
                self.log.info("SQLite: online backup %s → %s", src.name, dest.name)
                src_conn = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
                dst_conn = sqlite3.connect(dest)
                try:
                    src_conn.backup(dst_conn)
                finally:
                    dst_conn.close()
                    src_conn.close()
            else:
                shutil.copy2(src, dest)
            produced.append(str(dest))
        return produced

    def check(self) -> None:
        for p in self.databases:
            path = Path(p)
            if not path.exists():
                raise FileNotFoundError(f"SQLite DB not found: {path}")
            conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            try:
                row = conn.execute("PRAGMA integrity_check;").fetchone()
                if row[0] != "ok":
                    self.log.warning("SQLite %s integrity: %s", path.name, row[0])
                else:
                    self.log.info("SQLite OK: %s", path.name)
            finally:
                conn.close()
