"""Sources — where backuppy picks up files for processing."""
from .files import FilesSource

__all__ = ["FilesSource"]


def build_source(raw: dict, log) -> "FilesSource":
    """Factory: construct the right source instance from a dict."""
    stype = raw.get("type", "files")
    if stype == "files":
        return FilesSource(raw, log)
    raise ValueError(f"Unknown source type: {stype!r}")
