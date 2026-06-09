"""PostgreSQL trigger: runs pg_dump or pg_dumpall, writes to output_dir."""
from __future__ import annotations

import datetime as dt
import logging
import os
import subprocess
from pathlib import Path

from .base import BaseTrigger


def _run(cmd: list[str], env: dict, timeout: int | None = None) -> tuple[int, str, str]:
    """Run a subprocess, return (returncode, stdout, stderr) as strings.

    Captures bytes and decodes with errors='replace' so a non-UTF-8
    response (e.g. WIN1251 from PostgreSQL on Windows) doesn't crash
    the trigger. Tries common Windows encoding as fallback for stderr
    which often contains localized error messages.
    """
    res = subprocess.run(cmd, capture_output=True, env=env, timeout=timeout)
    stdout = _decode(res.stdout)
    stderr = _decode(res.stderr)
    return res.returncode, stdout, stderr


def _decode(data: bytes) -> str:
    """Decode bytes to str, trying UTF-8 first then Windows-1251."""
    if not data:
        return ""
    # Try UTF-8 (most modern systems)
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        pass
    # Try Windows-1251 (cyrillic-speaking Windows installs)
    try:
        return data.decode("cp1251")
    except UnicodeDecodeError:
        pass
    # Last resort: replace bad bytes
    return data.decode("utf-8", errors="replace")


class PostgresTrigger(BaseTrigger):
    type = "postgres"

    def __init__(self, cfg: dict, log: logging.Logger):
        super().__init__(cfg, log)
        self.output_dir: str = cfg["output_dir"]
        self.host: str = cfg.get("host", "localhost")
        self.port: int = int(cfg.get("port", 5432))
        self.username: str = cfg.get("username", "postgres")
        self.password: str = cfg.get("password", "")
        self.databases: list[str] = cfg.get("databases", [])
        self.format: str = cfg.get("format", "custom")
        self.extra_args: list[str] = cfg.get("extra_args", [])
        self.pg_dump_path: str = cfg.get("pg_dump_path", "pg_dump")
        self.pg_dumpall_path: str = cfg.get("pg_dumpall_path", "pg_dumpall")

    def _env(self) -> dict[str, str]:
        env = dict(os.environ)
        if self.password:
            env["PGPASSWORD"] = self.password
        # Force psql to emit messages in UTF-8 when possible.
        # PostgreSQL respects PGCLIENTENCODING for the client side.
        env.setdefault("PGCLIENTENCODING", "UTF8")
        return env

    def _common_args(self) -> list[str]:
        return [
            "-h", self.host, "-p", str(self.port),
            "-U", self.username, "--no-password",
        ]

    def _format_arg(self) -> tuple[str, str]:
        return {
            "custom":    ("c", ".dump"),
            "plain":     ("p", ".sql"),
            "tar":       ("t", ".tar"),
            "directory": ("d", ".d"),
        }.get(self.format.lower(), ("c", ".dump"))

    def run(self) -> list[str]:
        out_dir = Path(self.output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        timestamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        produced: list[str] = []

        if not self.databases:
            # Whole cluster
            out = out_dir / f"cluster-full-{timestamp}.sql"
            cmd = [self.pg_dumpall_path, *self._common_args(),
                   "-f", str(out), *self.extra_args]
            self.log.info("Postgres: pg_dumpall → %s", out.name)
            rc, _stdout, stderr = _run(cmd, self._env())
            if rc != 0:
                raise RuntimeError(f"pg_dumpall failed: {stderr.strip()}")
            produced.append(str(out))
        else:
            fmt_flag, ext = self._format_arg()
            for db in self.databases:
                out = out_dir / f"{db}-full-{timestamp}{ext}"
                cmd = [self.pg_dump_path, *self._common_args(),
                       "-F", fmt_flag, "-d", db,
                       "-f", str(out), *self.extra_args]
                self.log.info("Postgres: pg_dump %s → %s", db, out.name)
                rc, _stdout, stderr = _run(cmd, self._env())
                if rc != 0:
                    raise RuntimeError(f"pg_dump failed: {stderr.strip()}")
                produced.append(str(out))
        return produced

    def check(self) -> None:
        cmd = ["psql", *self._common_args(), "-d", "postgres",
               "-tAc", "SELECT version();"]
        try:
            rc, stdout, stderr = _run(cmd, self._env(), timeout=15)
        except FileNotFoundError:
            raise RuntimeError("psql not found. apt install postgresql-client")
        if rc != 0:
            raise RuntimeError(f"Postgres connection failed: {stderr.strip()}")
        self.log.info("Postgres OK: %s", stdout.strip().split("\n")[0])
