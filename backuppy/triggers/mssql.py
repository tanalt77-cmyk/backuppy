"""MSSQL trigger: connects to a SQL Server over TCP/1433 and runs BACKUP DATABASE.

The trigger writes the .bak file(s) to a directory on the Windows side. Whether
backuppy can later pick them up depends on the USER having mounted that directory
on Linux somehow (SMB, NFS, sshfs — backuppy doesn't care).

Config example:

  triggers:
    - type: mssql
      host: "10.0.0.5"
      port: 1433
      username: "backup_user"
      password: "SECRET"
      output_dir_windows: "D:\\Backups\\archive_sql\\local"
      databases:
        - { name: AppDB, backup_type: FULL }
        - { name: OtherDB, backup_type: DIFFERENTIAL }
      compression: true
      checksum: true
      copy_only: false
      timeout: 7200
      encrypt_connection: true
      trust_server_certificate: true
"""
from __future__ import annotations

import datetime as dt
import logging

from .base import BaseTrigger


class MSSQLTrigger(BaseTrigger):
    type = "mssql"

    def __init__(self, cfg: dict, log: logging.Logger):
        super().__init__(cfg, log)
        # Required
        self.host: str = cfg["host"]
        self.username: str = cfg["username"]
        self.password: str = cfg["password"]
        self.output_dir_windows: str = cfg["output_dir_windows"]
        self.databases: list[dict] = cfg["databases"]
        if not self.databases:
            raise ValueError("mssql trigger: 'databases' is required and non-empty")
        # Optional
        self.port: int = int(cfg.get("port", 1433))
        self.compression: bool = bool(cfg.get("compression", True))
        self.checksum: bool = bool(cfg.get("checksum", True))
        self.copy_only: bool = bool(cfg.get("copy_only", False))
        self.timeout: int = int(cfg.get("timeout", 7200))
        self.encrypt_connection: bool = bool(cfg.get("encrypt_connection", True))
        self.trust_server_certificate: bool = bool(
            cfg.get("trust_server_certificate", True)
        )

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
            f"SERVER={self.host},{self.port};"
            f"UID={self.username};PWD={self.password};"
        )
        if self.encrypt_connection:
            conn_str += "Encrypt=yes;"
        if self.trust_server_certificate:
            conn_str += "TrustServerCertificate=yes;"

        conn = pyodbc.connect(conn_str, timeout=30, autocommit=True)
        conn.timeout = self.timeout
        return conn

    def _remote_path(self, filename: str) -> str:
        base = self.output_dir_windows.rstrip("\\/").replace("/", "\\")
        return f"{base}\\{filename}"

    def _backup_one(self, conn, db: dict, timestamp: str) -> str:
        name = db["name"]
        bt = db.get("backup_type", "FULL").upper()
        if bt not in ("FULL", "DIFFERENTIAL", "LOG"):
            raise ValueError(
                f"Unknown backup_type: {bt}. "
                f"Valid: FULL, DIFFERENTIAL, LOG"
            )

        # File extension and suffix differ for transaction log backups
        if bt == "LOG":
            suffix = "log"
            ext = "trn"      # standard MSSQL transaction log extension
        elif bt == "DIFFERENTIAL":
            suffix = "diff"
            ext = "bak"
        else:
            suffix = "full"
            ext = "bak"

        filename = f"{name}-{suffix}-{timestamp}.{ext}"
        remote = self._remote_path(filename)

        opts = []
        if bt == "DIFFERENTIAL":
            opts.append("DIFFERENTIAL")
        opts.extend(["INIT", "FORMAT", "SKIP"])
        if self.compression:
            opts.append("COMPRESSION")
        if self.checksum:
            opts.append("CHECKSUM")
        if self.copy_only and bt == "FULL":
            opts.append("COPY_ONLY")
        opts.append(f"NAME = N'{name} {bt} backup'")

        safe_name = name.replace("]", "]]")
        # LOG backups use BACKUP LOG, others use BACKUP DATABASE
        cmd = "BACKUP LOG" if bt == "LOG" else "BACKUP DATABASE"
        sql = (
            f"{cmd} [{safe_name}] "
            f"TO DISK = N'{remote}' "
            f"WITH {', '.join(opts)};"
        )
        self.log.info("MSSQL: %s %s → %s", bt, name, remote)
        self.log.debug("SQL: %s", sql)

        cur = conn.cursor()
        cur.execute(sql)
        while cur.nextset():
            pass
        cur.close()
        self.log.info("MSSQL: BACKUP completed for %s", name)
        return remote

    def run(self) -> list[str]:
        timestamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        produced: list[str] = []
        conn = self._connect()
        try:
            for db in self.databases:
                produced.append(self._backup_one(conn, db, timestamp))
        finally:
            conn.close()
        return produced

    def check(self) -> None:
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute("SELECT @@VERSION;")
            version = cur.fetchone()[0]
            self.log.info("MSSQL OK: %s", version.split("\n")[0])
            for db in self.databases:
                cur.execute(
                    "SELECT state_desc, recovery_model_desc "
                    "FROM sys.databases WHERE name = ?;",
                    db["name"],
                )
                row = cur.fetchone()
                if not row:
                    raise RuntimeError(f"Database not found: {db['name']}")
                state, recovery = row[0], row[1]
                if state != "ONLINE":
                    self.log.warning(
                        "Database %s is %s (expected ONLINE)",
                        db["name"], state
                    )
                # LOG backups require FULL or BULK_LOGGED recovery model
                bt = db.get("backup_type", "FULL").upper()
                if bt == "LOG" and recovery == "SIMPLE":
                    raise RuntimeError(
                        f"Database '{db['name']}' is in SIMPLE recovery model — "
                        f"BACKUP LOG is not allowed. Change to FULL recovery: "
                        f"ALTER DATABASE [{db['name']}] SET RECOVERY FULL;"
                    )
            cur.close()
        finally:
            conn.close()
