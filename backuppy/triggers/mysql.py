"""MySQL/MariaDB trigger: runs mysqldump, writes to output_dir."""
from __future__ import annotations

import datetime as dt
import logging
import subprocess
import tempfile
from pathlib import Path

from .base import BaseTrigger


class MySQLTrigger(BaseTrigger):
    type = "mysql"

    def __init__(self, cfg: dict, log: logging.Logger):
        super().__init__(cfg, log)
        self.output_dir: str = cfg["output_dir"]
        self.host: str = cfg.get("host", "localhost")
        self.port: int = int(cfg.get("port", 3306))
        self.username: str = cfg.get("username", "root")
        self.password: str = cfg.get("password", "")
        self.databases: list[str] = cfg.get("databases", [])
        self.single_transaction: bool = bool(cfg.get("single_transaction", True))
        self.routines: bool = bool(cfg.get("routines", True))
        self.triggers_flag: bool = bool(cfg.get("triggers", True))
        self.events: bool = bool(cfg.get("events", True))
        self.extra_args: list[str] = cfg.get("extra_args", [])
        self.mysqldump_path: str = cfg.get("mysqldump_path", "mysqldump")
        # When True, mysqldump writes to a static filename like 'appdb-full.sql'
        # (no timestamp). Each backup OVERWRITES the previous one on disk.
        # Pair with sources.rename_with_timestamp to add the timestamp on upload.
        self.static_local_name: bool = bool(cfg.get("static_local_name", False))

    def _defaults_file(self) -> Path:
        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".cnf", delete=False, prefix="backuppy-mysql-"
        )
        tmp.write(
            "[client]\n"
            f"host = {self.host}\n"
            f"port = {self.port}\n"
            f"user = {self.username}\n"
            f"password = {self.password}\n"
        )
        tmp.close()
        Path(tmp.name).chmod(0o600)
        return Path(tmp.name)

    def _base_args(self, defaults_file: Path) -> list[str]:
        args = [self.mysqldump_path, f"--defaults-file={defaults_file}"]
        if self.single_transaction:
            args.append("--single-transaction")
        if self.routines:
            args.append("--routines")
        if self.triggers_flag:
            args.append("--triggers")
        if self.events:
            args.append("--events")
        args.extend(self.extra_args)
        return args

    def run(self) -> list[str]:
        out_dir = Path(self.output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        timestamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        produced: list[str] = []
        defaults_file = self._defaults_file()
        try:
            if not self.databases:
                if self.static_local_name:
                    out = out_dir / "all-databases-full.sql"
                else:
                    out = out_dir / f"all-databases-full-{timestamp}.sql"
                cmd = [*self._base_args(defaults_file), "--all-databases"]
                self.log.info("MySQL: --all-databases → %s", out.name)
                with open(out, "wb") as fh:
                    res = subprocess.run(cmd, stdout=fh, stderr=subprocess.PIPE)
                if res.returncode != 0:
                    raise RuntimeError(
                        f"mysqldump failed: {res.stderr.decode().strip()}"
                    )
                produced.append(str(out))
            else:
                for db in self.databases:
                    if self.static_local_name:
                        out = out_dir / f"{db}-full.sql"
                    else:
                        out = out_dir / f"{db}-full-{timestamp}.sql"
                    cmd = [*self._base_args(defaults_file), db]
                    self.log.info("MySQL: %s → %s", db, out.name)
                    with open(out, "wb") as fh:
                        res = subprocess.run(cmd, stdout=fh, stderr=subprocess.PIPE)
                    if res.returncode != 0:
                        raise RuntimeError(
                            f"mysqldump failed: {res.stderr.decode().strip()}"
                        )
                    produced.append(str(out))
        finally:
            defaults_file.unlink(missing_ok=True)
        return produced

    def check(self) -> None:
        defaults_file = self._defaults_file()
        try:
            cmd = ["mysql", f"--defaults-file={defaults_file}",
                   "-e", "SELECT VERSION();"]
            try:
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            except FileNotFoundError:
                raise RuntimeError(
                    "mysql client not found. apt install default-mysql-client"
                )
            if res.returncode != 0:
                raise RuntimeError(f"MySQL connection failed: {res.stderr.strip()}")
            self.log.info("MySQL OK: %s", res.stdout.strip().split("\n")[-1])
        finally:
            defaults_file.unlink(missing_ok=True)
