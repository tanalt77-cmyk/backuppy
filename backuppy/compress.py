"""Compression methods for backup files."""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from .config import CompressionCfg


def compress_file(path: Path, cfg: CompressionCfg, log: logging.Logger) -> Path:
    """Compress a file in-place (well, alongside, then remove original).
    Returns the path of the compressed file."""
    method = cfg.method.lower()
    if method == "none":
        return path

    log.info("Compressing with %s%s: %s",
             method,
             f" (level={cfg.level})" if cfg.level else "",
             path.name)

    if method == "gzip":
        return _run(path, "gzip", ".gz", cfg.level, log, ["-f"])
    if method == "bzip2":
        return _run(path, "bzip2", ".bz2", cfg.level, log, ["-f"])
    if method == "xz":
        return _run(path, "xz", ".xz", cfg.level, log, ["-f", "-T", "0"])  # -T 0 = use all cores
    if method == "zstd":
        # zstd levels: 1-19 (normal), 20-22 (ultra)
        return _run(path, "zstd", ".zst", cfg.level, log,
                    ["--rm", "-f", "-T0"])  # --rm removes source on success

    raise ValueError(f"Unknown compression method: {method}")


def _run(path: Path, binary: str, suffix: str, level: int | None,
         log: logging.Logger, extra_args: list[str]) -> Path:
    cmd = [binary]
    if level is not None:
        cmd.append(f"-{level}")
    cmd.extend(extra_args)
    cmd.append(str(path))

    log.debug("Running: %s", " ".join(cmd))
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except FileNotFoundError:
        raise RuntimeError(
            f"{binary} not found. Install it (apt install {binary}) "
            f"or change compression.method."
        )

    out = path.with_suffix(path.suffix + suffix)
    if res.returncode != 0:
        raise RuntimeError(f"{binary} failed: {res.stderr.strip()}")

    # gzip/bzip2/xz remove the original automatically with -f and rename to .ext
    # zstd needs --rm
    if not out.exists():
        # If for some reason original wasn't replaced, check current state
        if path.exists():
            raise RuntimeError(
                f"{binary} succeeded but expected output {out} not found"
            )

    size_mb = out.stat().st_size / 1024 / 1024
    log.info("  → %s (%.2f MB)", out.name, size_mb)
    return out


def file_extension_for(method: str) -> str:
    """Used by orchestrator to know final filename suffix."""
    return {
        "gzip": ".gz",
        "bzip2": ".bz2",
        "xz": ".xz",
        "zstd": ".zst",
        "none": "",
    }.get(method.lower(), "")
