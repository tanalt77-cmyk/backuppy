"""PostgreSQL dumper using pg_dump / pg_dumpall."""
from __future__ import annotations

import datetime as dt
import logging
import os
import subprocess
from pathlib import Path

from ..config import PostgresCfg
from .base import BaseDumper


class PostgresDumper(BaseDumper):
    def __init__(self, cfg: PostgresCfg, log: logging.Logger):
        self.cfg = cfg
        self.log = log

    def _env(self) -> dict[str, str]:
        env = dict(os.environ)
        if self.cfg.password:
            env["PGPASSWORD"] = self.cfg.password
        return env

    def _common_args(self) -> list[str]:
        return [
            "-h", self.cfg.host,
            "-p", str(self.cfg.port),
            "-U", self.cfg.username,
            "--no-password",  # rely on PGPASSWORD/.pgpass; fail loudly if missing
        ]

    def _format_arg(self) -> tuple[str, str]:
        """Returns (pg_dump format flag value, file extension)."""
        fmt = self.cfg.format.lower()
        return {
            "custom":    ("c", ".dump"),     # pg_restore-friendly, compressed
            "plain":     ("p", ".sql"),
            "tar":       ("t", ".tar"),
            "directory": ("d", ".d"),        # results in a directory, not a file
        }.get(fmt, ("c", ".dump"))

    def _dump_one(self, db_name: str, work_dir: Path, timestamp: str) -> Path:
        fmt_flag, ext = self._format_arg()
        out = work_dir / f"{db_name}-full-{timestamp}{ext}"

        cmd = [self.cfg.pg_dump_path, *self._common_args(),
               "-F", fmt_flag, "-d", db_name,
               "-f", str(out), *self.cfg.extra_args]

        self.log.info("Postgres: dumping %s → %s", db_name, out.name)
        res = subprocess.run(cmd, capture_output=True, text=True, env=self._env())
        if res.returncode != 0:
            raise RuntimeError(f"pg_dump failed: {res.stderr.strip()}")

        if out.is_file():
            size_mb = out.stat().st_size / 1024 / 1024
            self.log.info("  → %s (%.2f MB)", out.name, size_mb)
        return out

    def _dump_all_globals_and_dbs(self, work_dir: Path, timestamp: str) -> list[Path]:
        """When databases list is empty, use pg_dumpall (clustering all DBs + globals)."""
        out = work_dir / f"cluster-full-{timestamp}.sql"
        cmd = [self.cfg.pg_dumpall_path, *self._common_args(),
               "-f", str(out), *self.cfg.extra_args]

        self.log.info("Postgres: dumping entire cluster → %s", out.name)
        res = subprocess.run(cmd, capture_output=True, text=True, env=self._env())
        if res.returncode != 0:
            raise RuntimeError(f"pg_dumpall failed: {res.stderr.strip()}")

        size_mb = out.stat().st_size / 1024 / 1024
        self.log.info("  → %s (%.2f MB)", out.name, size_mb)
        return [out]

    def dump_all(self, work_dir: Path) -> list[Path]:
        timestamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        if not self.cfg.databases:
            return self._dump_all_globals_and_dbs(work_dir, timestamp)
        return [self._dump_one(db, work_dir, timestamp) for db in self.cfg.databases]

    def check_connection(self) -> None:
        # Use psql -c '\l' to test auth + list visible DBs
        cmd = ["psql", *self._common_args(), "-d", "postgres",
               "-tAc", "SELECT version();"]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True,
                                 env=self._env(), timeout=15)
        except FileNotFoundError:
            raise RuntimeError("psql not found. apt install postgresql-client")
        if res.returncode != 0:
            raise RuntimeError(f"Postgres connection failed: {res.stderr.strip()}")
        self.log.info("Postgres OK: %s", res.stdout.strip().split("\n")[0])

        # Verify each named DB exists
        for db in self.cfg.databases:
            cmd = ["psql", *self._common_args(), "-d", "postgres", "-tAc",
                   f"SELECT 1 FROM pg_database WHERE datname='{db}';"]
            res = subprocess.run(cmd, capture_output=True, text=True, env=self._env())
            if res.stdout.strip() != "1":
                raise RuntimeError(f"Postgres database not found: {db}")

    def prefixes(self) -> list[str]:
        if not self.cfg.databases:
            return ["cluster-full-"]
        return [f"{db}-full-" for db in self.cfg.databases]
