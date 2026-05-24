"""Splitter: break large files into N-MB chunks for upload."""
from __future__ import annotations

import logging
from pathlib import Path

from .config import SplitterCfg


def split_file(path: Path, cfg: SplitterCfg, log: logging.Logger) -> list[Path]:
    """Split path into part files: name.part001, name.part002, ...
    Removes original on success. Returns list of part paths (or [path] if
    splitting wasn't needed)."""
    if not cfg.enabled:
        return [path]

    size = path.stat().st_size
    if cfg.only_above_mb > 0 and size < cfg.only_above_mb * 1024 * 1024:
        return [path]

    chunk_bytes = cfg.chunk_size_mb * 1024 * 1024
    if size <= chunk_bytes:
        return [path]

    parts: list[Path] = []
    total_parts = (size + chunk_bytes - 1) // chunk_bytes
    width = max(3, len(str(total_parts)))
    log.info("Splitter: %s (%.1f MB) → %d parts of %d MB",
             path.name, size / 1024 / 1024, total_parts, cfg.chunk_size_mb)

    with open(path, "rb") as src:
        idx = 1
        while True:
            data = src.read(chunk_bytes)
            if not data:
                break
            part_path = path.with_name(f"{path.name}.part{idx:0{width}d}")
            with open(part_path, "wb") as out:
                out.write(data)
            parts.append(part_path)
            idx += 1

    path.unlink()
    log.info("Splitter: created %d parts, removed original", len(parts))
    return parts


def merge_parts(parts: list[Path], output: Path) -> None:
    """Reconstruct original file from parts. Useful for restore documentation."""
    parts_sorted = sorted(parts, key=lambda p: p.name)
    with open(output, "wb") as out:
        for p in parts_sorted:
            with open(p, "rb") as src:
                while chunk := src.read(8 * 1024 * 1024):
                    out.write(chunk)
