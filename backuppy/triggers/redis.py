"""Redis trigger: triggers BGSAVE, then copies the RDB to output_dir."""
from __future__ import annotations

import datetime as dt
import logging
import shutil
import subprocess
import time
from pathlib import Path

from .base import BaseTrigger


class RedisTrigger(BaseTrigger):
    type = "redis"

    def __init__(self, cfg: dict, log: logging.Logger):
        super().__init__(cfg, log)
        self.output_dir: str = cfg["output_dir"]
        self.host: str = cfg.get("host", "localhost")
        self.port: int = int(cfg.get("port", 6379))
        self.password: str = cfg.get("password", "")
        self.rdb_path: str = cfg.get("rdb_path", "/var/lib/redis/dump.rdb")
        self.use_bgsave: bool = bool(cfg.get("use_bgsave", True))
        self.save_timeout: int = int(cfg.get("save_timeout", 300))
        self.redis_cli_path: str = cfg.get("redis_cli_path", "redis-cli")
        # When True, output uses static filename 'redis-full.rdb' (no
        # timestamp). Each backup OVERWRITES the previous one on disk.
        # Pair with sources.rename_with_timestamp on upload.
        self.static_local_name: bool = bool(cfg.get("static_local_name", False))

    def _cli(self, *args: str) -> str:
        cmd = [self.redis_cli_path, "-h", self.host, "-p", str(self.port)]
        if self.password:
            cmd.extend(["-a", self.password, "--no-auth-warning"])
        cmd.extend(args)
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            raise RuntimeError(f"redis-cli failed: {res.stderr.strip()}")
        return res.stdout.strip()

    def _wait_bgsave(self) -> None:
        before = int(self._cli("LASTSAVE"))
        self._cli("BGSAVE")
        deadline = time.time() + self.save_timeout
        while time.time() < deadline:
            after = int(self._cli("LASTSAVE"))
            if after > before:
                return
            time.sleep(1)
        raise RuntimeError(f"BGSAVE did not finish within {self.save_timeout}s")

    def run(self) -> list[str]:
        out_dir = Path(self.output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        timestamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")

        if self.use_bgsave:
            self.log.info("Redis: BGSAVE")
            self._wait_bgsave()

        src = Path(self.rdb_path)
        if not src.exists():
            raise FileNotFoundError(f"RDB file not found: {src}")

        if self.static_local_name:
            dest = out_dir / "redis-full.rdb"
        else:
            dest = out_dir / f"redis-full-{timestamp}.rdb"
        shutil.copy2(src, dest)
        self.log.info("Redis: %s → %s", src.name, dest.name)
        return [str(dest)]

    def check(self) -> None:
        out = self._cli("PING")
        if out != "PONG":
            raise RuntimeError(f"Redis PING returned: {out!r}")
        self.log.info("Redis OK")
