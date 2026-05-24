"""HookTrigger: runs an arbitrary shell command.

Use case: rsync from a remote machine, custom DB dumps, anything that produces
files somewhere. Just run the command and trust the user to put files where
sources will pick them up.

Config:
  triggers:
    - type: hook
      command: "rsync -av server:/data /tmp/staging/"
      output_dir: /tmp/staging      # informational only; not enforced
      timeout: 3600                 # seconds, optional
      shell: true                   # if false, command must be a list
      hard_error: true              # non-zero exit = abort (default: true)
"""
from __future__ import annotations

import logging
import subprocess

from .base import BaseTrigger


class HookTrigger(BaseTrigger):
    type = "hook"

    def __init__(self, cfg: dict, log: logging.Logger):
        super().__init__(cfg, log)
        self.command = cfg["command"]
        self.output_dir: str = cfg.get("output_dir", "")
        self.timeout: int | None = cfg.get("timeout")
        self.shell: bool = bool(cfg.get("shell", True))
        self.hard_error: bool = bool(cfg.get("hard_error", True))

    def run(self) -> list[str]:
        cmd_display = self.command if isinstance(self.command, str) else " ".join(self.command)
        self.log.info("Hook: $ %s", cmd_display)
        try:
            res = subprocess.run(
                self.command,
                shell=self.shell,
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"Hook timed out after {self.timeout}s: {cmd_display}")

        if res.stdout.strip():
            self.log.info("  stdout: %s", res.stdout.strip()[:500])
        if res.returncode != 0:
            msg = f"hook failed (exit={res.returncode}): {cmd_display}\n{res.stderr.strip()}"
            if self.hard_error:
                raise RuntimeError(msg)
            self.log.warning(msg)
        return [self.output_dir] if self.output_dir else []

    def check(self) -> None:
        # No-op: we can't safely test-run an arbitrary command
        pass
