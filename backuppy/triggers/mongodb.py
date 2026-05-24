"""MongoDB trigger: runs mongodump, writes to output_dir as one tar per DB."""
from __future__ import annotations

import datetime as dt
import logging
import shutil
import subprocess
import tarfile
from pathlib import Path

from .base import BaseTrigger


class MongoTrigger(BaseTrigger):
    type = "mongodb"

    def __init__(self, cfg: dict, log: logging.Logger):
        super().__init__(cfg, log)
        self.output_dir: str = cfg["output_dir"]
        self.uri: str = cfg["uri"]
        self.databases: list[str] = cfg.get("databases", [])
        self.gzip: bool = bool(cfg.get("gzip", True))
        self.oplog: bool = bool(cfg.get("oplog", False))
        self.extra_args: list[str] = cfg.get("extra_args", [])
        self.mongodump_path: str = cfg.get("mongodump_path", "mongodump")

    def _base_args(self) -> list[str]:
        args = [self.mongodump_path, "--uri", self.uri]
        if self.gzip:
            args.append("--gzip")
        if self.oplog:
            args.append("--oplog")
        args.extend(self.extra_args)
        return args

    def _dump_to_tar(self, out_dir: Path, db_name: str | None,
                    timestamp: str) -> Path:
        label = db_name if db_name else "alldbs"
        staging = out_dir / f"mongo-{label}-{timestamp}-staging"
        staging.mkdir()

        cmd = [*self._base_args(), "--out", str(staging)]
        if db_name:
            cmd.extend(["--db", db_name])

        self.log.info("Mongo: dumping %s → tmp", label)
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            raise RuntimeError(f"mongodump failed: {res.stderr.strip()}")

        tar_path = out_dir / f"{label}-full-{timestamp}.tar"
        with tarfile.open(tar_path, "w") as tar:
            tar.add(staging, arcname=staging.name)
        shutil.rmtree(staging)
        self.log.info("Mongo: tar → %s", tar_path.name)
        return tar_path

    def run(self) -> list[str]:
        out_dir = Path(self.output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        timestamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        produced: list[str] = []
        if not self.databases:
            produced.append(str(self._dump_to_tar(out_dir, None, timestamp)))
        else:
            for db in self.databases:
                produced.append(str(self._dump_to_tar(out_dir, db, timestamp)))
        return produced

    def check(self) -> None:
        cmd = ["mongosh", self.uri, "--quiet",
               "--eval", "db.adminCommand({ping: 1})"]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        except FileNotFoundError:
            self.log.warning("mongosh not found; skipping connectivity check")
            return
        if res.returncode != 0:
            raise RuntimeError(f"Mongo connection failed: {res.stderr.strip()}")
        self.log.info("Mongo OK")
