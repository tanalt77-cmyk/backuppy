"""MongoDB dumper using mongodump."""
from __future__ import annotations

import datetime as dt
import logging
import shutil
import subprocess
import tarfile
from pathlib import Path

from ..config import MongoCfg
from .base import BaseDumper


class MongoDumper(BaseDumper):
    def __init__(self, cfg: MongoCfg, log: logging.Logger):
        self.cfg = cfg
        self.log = log

    def _base_args(self) -> list[str]:
        args = [self.cfg.mongodump_path, "--uri", self.cfg.uri]
        if self.cfg.gzip:
            args.append("--gzip")
        if self.cfg.oplog:
            args.append("--oplog")
        args.extend(self.cfg.extra_args)
        return args

    def _dump_to_dir(self, work_dir: Path, db_name: str | None,
                     timestamp: str) -> Path:
        """mongodump produces a directory tree; we tar it for storage."""
        label = db_name if db_name else "alldbs"
        out_dir = work_dir / f"mongo-{label}-{timestamp}"
        out_dir.mkdir()

        cmd = [*self._base_args(), "--out", str(out_dir)]
        if db_name:
            cmd.extend(["--db", db_name])

        self.log.info("Mongo: dumping %s → %s", label, out_dir.name)
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            raise RuntimeError(f"mongodump failed: {res.stderr.strip()}")

        # Pack into single tar (no compression — mongodump --gzip already compresses inside)
        tar_path = work_dir / f"{label}-full-{timestamp}.tar"
        with tarfile.open(tar_path, "w") as tar:
            tar.add(out_dir, arcname=out_dir.name)
        shutil.rmtree(out_dir)

        size_mb = tar_path.stat().st_size / 1024 / 1024
        self.log.info("  → %s (%.2f MB)", tar_path.name, size_mb)
        return tar_path

    def dump_all(self, work_dir: Path) -> list[Path]:
        timestamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        if not self.cfg.databases:
            return [self._dump_to_dir(work_dir, None, timestamp)]
        return [self._dump_to_dir(work_dir, db, timestamp)
                for db in self.cfg.databases]

    def check_connection(self) -> None:
        # mongodump --uri ... --dryRun would be ideal, but it requires actually
        # doing the dump in newer versions. Use mongosh ping instead.
        cmd = ["mongosh", self.cfg.uri, "--quiet",
               "--eval", "db.adminCommand({ping: 1})"]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        except FileNotFoundError:
            # Fall back to a tiny mongodump dry run
            self.log.warning("mongosh not found, skipping connectivity check")
            return
        if res.returncode != 0:
            raise RuntimeError(f"Mongo connection failed: {res.stderr.strip()}")
        self.log.info("Mongo OK")

    def prefixes(self) -> list[str]:
        if not self.cfg.databases:
            return ["alldbs-full-"]
        return [f"{db}-full-" for db in self.cfg.databases]
