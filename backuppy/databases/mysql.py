"""MySQL/MariaDB dumper using mysqldump."""
from __future__ import annotations

import datetime as dt
import logging
import subprocess
import tempfile
from pathlib import Path

from ..config import MySQLCfg
from .base import BaseDumper


class MySQLDumper(BaseDumper):
    def __init__(self, cfg: MySQLCfg, log: logging.Logger):
        self.cfg = cfg
        self.log = log

    def _defaults_file(self) -> Path:
        """Write a temporary [client] my.cnf to avoid the password-on-CLI warning."""
        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".cnf", delete=False, prefix="backuppy-mysql-"
        )
        tmp.write(
            "[client]\n"
            f"host = {self.cfg.host}\n"
            f"port = {self.cfg.port}\n"
            f"user = {self.cfg.username}\n"
            f"password = {self.cfg.password}\n"
        )
        tmp.close()
        Path(tmp.name).chmod(0o600)
        return Path(tmp.name)

    def _base_args(self, defaults_file: Path) -> list[str]:
        args = [self.cfg.mysqldump_path, f"--defaults-file={defaults_file}"]
        if self.cfg.single_transaction:
            args.append("--single-transaction")
        if self.cfg.routines:
            args.append("--routines")
        if self.cfg.triggers:
            args.append("--triggers")
        if self.cfg.events:
            args.append("--events")
        args.extend(self.cfg.extra_args)
        return args

    def _dump_one(self, db_name: str, work_dir: Path, timestamp: str,
                  defaults_file: Path) -> Path:
        out = work_dir / f"{db_name}-full-{timestamp}.sql"
        cmd = [*self._base_args(defaults_file), db_name]

        self.log.info("MySQL: dumping %s → %s", db_name, out.name)
        with open(out, "wb") as fh:
            res = subprocess.run(cmd, stdout=fh, stderr=subprocess.PIPE)
        if res.returncode != 0:
            raise RuntimeError(f"mysqldump failed: {res.stderr.decode().strip()}")

        size_mb = out.stat().st_size / 1024 / 1024
        self.log.info("  → %s (%.2f MB)", out.name, size_mb)
        return out

    def _dump_all(self, work_dir: Path, timestamp: str,
                  defaults_file: Path) -> list[Path]:
        out = work_dir / f"all-databases-full-{timestamp}.sql"
        cmd = [*self._base_args(defaults_file), "--all-databases"]

        self.log.info("MySQL: dumping all databases → %s", out.name)
        with open(out, "wb") as fh:
            res = subprocess.run(cmd, stdout=fh, stderr=subprocess.PIPE)
        if res.returncode != 0:
            raise RuntimeError(f"mysqldump failed: {res.stderr.decode().strip()}")
        size_mb = out.stat().st_size / 1024 / 1024
        self.log.info("  → %s (%.2f MB)", out.name, size_mb)
        return [out]

    def dump_all(self, work_dir: Path) -> list[Path]:
        timestamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        defaults_file = self._defaults_file()
        try:
            if not self.cfg.databases:
                return self._dump_all(work_dir, timestamp, defaults_file)
            return [self._dump_one(db, work_dir, timestamp, defaults_file)
                    for db in self.cfg.databases]
        finally:
            defaults_file.unlink(missing_ok=True)

    def check_connection(self) -> None:
        defaults_file = self._defaults_file()
        try:
            cmd = ["mysql", f"--defaults-file={defaults_file}",
                   "-e", "SELECT VERSION();"]
            try:
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            except FileNotFoundError:
                raise RuntimeError("mysql client not found. apt install default-mysql-client")
            if res.returncode != 0:
                raise RuntimeError(f"MySQL connection failed: {res.stderr.strip()}")
            self.log.info("MySQL OK: %s", res.stdout.strip().split("\n")[-1])

            for db in self.cfg.databases:
                cmd = ["mysql", f"--defaults-file={defaults_file}",
                       "-e", f"USE `{db}`;"]
                res = subprocess.run(cmd, capture_output=True, text=True)
                if res.returncode != 0:
                    raise RuntimeError(f"MySQL database not found: {db}")
        finally:
            defaults_file.unlink(missing_ok=True)

    def prefixes(self) -> list[str]:
        if not self.cfg.databases:
            return ["all-databases-full-"]
        return [f"{db}-full-" for db in self.cfg.databases]
