"""Base class for database dumpers."""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from pathlib import Path


class BaseDumper(ABC):
    """A dumper produces one or more files in work_dir representing the dump.

    The file naming convention is: {dbname}-{type}-{timestamp}.{ext}
    where {type} can be 'full', 'diff', etc., depending on the engine.
    """

    @abstractmethod
    def dump_all(self, work_dir: Path) -> list[Path]:
        """Run dumps. Return list of produced file paths inside work_dir."""

    @abstractmethod
    def check_connection(self) -> None:
        """Verify connectivity/credentials. Raise on failure."""

    @abstractmethod
    def prefixes(self) -> list[str]:
        """Return list of filename prefixes this dumper will produce.
        Used for storage rotation: each prefix gets its own keep_last group.
        Example: ['AppDB-full-', 'OtherDB-full-']
        """
