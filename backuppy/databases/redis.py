"""Redis dumper: trigger BGSAVE, then copy the RDB file."""
from __future__ import annotations

import datetime as dt
import logging
import shutil
import subprocess
import time
from pathlib import Path

from ..config import RedisCfg
from .base import BaseDumper


class RedisDumper(BaseDumper):
    def __init__(self, cfg: RedisCfg, log: logging.Logger):
        self.cfg = cfg
        self.log = log

    def _cli(self, *args: str, capture: bool = True) -> str:
        cmd = [self.cfg.redis_cli_path,
               "-h", self.cfg.host, "-p", str(self.cfg.port)]
        if self.cfg.password:
            cmd.extend(["-a", self.cfg.password, "--no-auth-warning"])
        cmd.extend(args)
        res = subprocess.run(cmd, capture_output=capture, text=True)
        if res.returncode != 0:
            raise RuntimeError(f"redis-cli failed: {res.stderr.strip()}")
        return res.stdout.strip() if capture else ""

    def _wait_bgsave(self) -> None:
        """Wait until LASTSAVE timestamp increases (= BGSAVE finished)."""
        before = int(self._cli("LASTSAVE"))
        self._cli("BGSAVE")
        deadline = time.time() + self.cfg.save_timeout
        while time.time() < deadline:
            after = int(self._cli("LASTSAVE"))
            if after > before:
                return
            time.sleep(1)
        raise RuntimeError(
            f"BGSAVE did not finish within {self.cfg.save_timeout}s"
        )

    def dump_all(self, work_dir: Path) -> list[Path]:
        timestamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")

        if self.cfg.use_bgsave:
            self.log.info("Redis: triggering BGSAVE")
            self._wait_bgsave()
            self.log.info("Redis: BGSAVE complete")

        src = Path(self.cfg.rdb_path)
        if not src.exists():
            raise FileNotFoundError(f"RDB file not found: {src}")

        dest = work_dir / f"redis-full-{timestamp}.rdb"
        shutil.copy2(src, dest)
        size_mb = dest.stat().st_size / 1024 / 1024
        self.log.info("Redis: copied RDB → %s (%.2f MB)", dest.name, size_mb)
        return [dest]

    def check_connection(self) -> None:
        out = self._cli("PING")
        if out != "PONG":
            raise RuntimeError(f"Redis PING returned: {out!r}")
        info = self._cli("INFO", "server")
        version = next((l.split(":")[1] for l in info.splitlines()
                        if l.startswith("redis_version:")), "?")
        self.log.info("Redis OK: v%s", version)

        if self.cfg.use_bgsave and not Path(self.cfg.rdb_path).parent.is_dir():
            raise RuntimeError(
                f"RDB directory does not exist: {Path(self.cfg.rdb_path).parent}"
            )

    def prefixes(self) -> list[str]:
        return ["redis-full-"]
