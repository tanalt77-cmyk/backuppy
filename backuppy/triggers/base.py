"""Base class for triggers."""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod


class BaseTrigger(ABC):
    """Base for all triggers. A trigger PRODUCES files somewhere; sources later
    pick them up.

    The trigger's job ends when files are on disk in their configured output_dir.
    Pickup, processing and uploading is the job of sources + the orchestrator.
    """
    type = "base"

    def __init__(self, cfg: dict, log: logging.Logger):
        self.cfg = cfg
        self.log = log

    @abstractmethod
    def run(self) -> list[str]:
        """Execute the trigger. Return list of paths produced (informational)."""

    def check(self) -> None:
        """Preflight check. Raise on failure. Default: no-op."""
