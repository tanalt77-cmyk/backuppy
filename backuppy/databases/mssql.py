"""Microsoft SQL Server dumper.

Runs BACKUP DATABASE on a remote Windows SQL Server via TCP/1433. SQL Server
writes the .bak file to its local disk; Linux picks it up via a mounted SMB share.
"""
from __future__ import annotations

import datetime as dt
import logging
import os
import shutil
from pathlib import Path

from ..config import MSSQLCfg, MSSQLDatabase
from .base import BaseDumper


class MSSQLDumper(BaseDumper):
    def __init__(self, cfg: MSSQLCfg, log: logging.Logger):
        self.cfg = cfg
        self.log = log

    def _connect(self):
        try:
            import pyodbc
        except ImportError as e:
            raise RuntimeError(
                "pyodbc not installed. Run:\n"
                "  apt install unixodbc unixodbc-dev\n"
                "  pip install pyodbc"
            ) from e

        drivers = [d for d in pyodbc.drivers() if "SQL Server" in d]
        preferred = [
            "ODBC Driver 18 for SQL Server",
            "ODBC Driver 17 for SQL Server",
        ]
        driver = next((d for d in preferred if d in drivers), None)
        if not driver:
            raise RuntimeError(
                f"ODBC Driver 17/18 for SQL Server not found. Installed: {drivers}"
            )

        conn_str = (
            f"DRIVER={{{driver}}};"
            f"SERVER={self.cfg.host},{self.cfg.port};"
            f"UID={self.cfg.username};PWD={self.cfg.password};"
        )
        if self.cfg.encrypt_connection:
            conn_str += "Encrypt=yes;"
        if self.cfg.trust_server_certificate:
            conn_str += "TrustServerCertificate=yes;"

        self.log.debug("MSSQL connect: %s:%d as %s (driver=%s)",
                       self.cfg.host, self.cfg.port, self.cfg.username, driver)
        conn = pyodbc.connect(conn_str, timeout=30, autocommit=True)
        conn.timeout = self.cfg.timeout
        return conn

    def _remote_path(self, filename: str) -> str:
        base = self.cfg.remote_backup_dir.rstrip("\\/").replace("/", "\\")
        return f"{base}\\{filename}"

    def _local_path(self, filename: str) -> Path:
        return Path(self.cfg.local_mount_dir) / filename

    def _backup_one(self, conn, db: MSSQLDatabase, timestamp: str) -> Path:
        bt = db.backup_type.upper()
        if bt not in ("FULL", "DIFFERENTIAL"):
            raise ValueError(f"Unknown backup_type: {bt}")

        suffix = "diff" if bt == "DIFFERENTIAL" else "full"
        filename = f"{db.name}-{suffix}-{timestamp}.bak"
        remote = self._remote_path(filename)

        opts = []
        if bt == "DIFFERENTIAL":
            opts.append("DIFFERENTIAL")
        opts.extend(["INIT", "FORMAT", "SKIP"])
        if self.cfg.compression:
            opts.append("COMPRESSION")
        if self.cfg.checksum:
            opts.append("CHECKSUM")
        if self.cfg.copy_only and bt == "FULL":
            opts.append("COPY_ONLY")
        opts.append(f"NAME = N'{db.name} {bt} backup'")

        safe_name = db.name.replace("]", "]]")
        sql = (
            f"BACKUP DATABASE [{safe_name}] "
            f"TO DISK = N'{remote}' "
            f"WITH {', '.join(opts)};"
        )
        self.log.info("MSSQL: %s %s → %s", bt, db.name, remote)
        self.log.debug("SQL: %s", sql)

        cur = conn.cursor()
        cur.execute(sql)
        while cur.nextset():
            pass
        cur.close()

        local = self._local_path(filename)
        if not local.exists():
            raise RuntimeError(
                f"BACKUP succeeded but file did not appear via SMB: {local}\n"
                f"Check that local_mount_dir matches remote_backup_dir."
            )
        size_mb = local.stat().st_size / 1024 / 1024
        self.log.info("MSSQL: done %s (%.2f MB)", filename, size_mb)
        return local

    def dump_all(self, work_dir: Path) -> list[Path]:
        if not self.cfg.databases:
            return []

        timestamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        results: list[Path] = []

        conn = self._connect()
        try:
            for db in self.cfg.databases:
                produced = self._backup_one(conn, db, timestamp)
                dest = work_dir / produced.name
                shutil.move(str(produced), str(dest))
                results.append(dest)
                if self.cfg.cleanup_remote and produced.exists():
                    try:
                        produced.unlink()
                    except OSError as e:
                        self.log.warning("Could not remove %s: %s", produced, e)
        finally:
            conn.close()

        return results

    def check_connection(self) -> None:
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute("SELECT @@VERSION;")
            version = cur.fetchone()[0]
            self.log.info("MSSQL OK: %s", version.split("\n")[0])
            for db in self.cfg.databases:
                cur.execute(
                    "SELECT state_desc FROM sys.databases WHERE name = ?;",
                    db.name,
                )
                row = cur.fetchone()
                if not row:
                    raise RuntimeError(f"Database not found: {db.name}")
                if row[0] != "ONLINE":
                    self.log.warning(
                        "Database %s is %s (expected ONLINE)", db.name, row[0]
                    )
            cur.close()
        finally:
            conn.close()

        if not Path(self.cfg.local_mount_dir).is_dir():
            raise RuntimeError(
                f"local_mount_dir does not exist or is not a directory: "
                f"{self.cfg.local_mount_dir}\nCheck the SMB mount."
            )
        if not os.access(self.cfg.local_mount_dir, os.R_OK):
            raise RuntimeError(
                f"No read access to {self.cfg.local_mount_dir} from Linux."
            )
        self.log.info("SMB mount OK: %s", self.cfg.local_mount_dir)

    def prefixes(self) -> list[str]:
        result = []
        for db in self.cfg.databases:
            suffix = "diff" if db.backup_type.upper() == "DIFFERENTIAL" else "full"
            result.append(f"{db.name}-{suffix}-")
        return result
