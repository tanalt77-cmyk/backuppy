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

    # Extra args below are for STREAM mode (compressor reads stdin, writes
    # stdout — see _run). File-mode flags like -f/--rm are intentionally absent:
    # in stream mode there is no in-place file to force or remove, and _run
    # deletes the source itself after a clean exit.
    if method == "gzip":
        return _run(path, "gzip", ".gz", cfg.level, log, [])
    if method == "bzip2":
        return _run(path, "bzip2", ".bz2", cfg.level, log, [])
    if method == "xz":
        return _run(path, "xz", ".xz", cfg.level, log, ["-T", "0"])  # -T 0 = all cores
    if method == "zstd":
        # zstd levels: 1-19 (normal), 20-22 (ultra)
        return _run(path, "zstd", ".zst", cfg.level, log, ["-T0"])

    raise ValueError(f"Unknown compression method: {method}")


def _run(path: Path, binary: str, suffix: str, level: int | None,
         log: logging.Logger, extra_args: list[str]) -> Path:
    """Compress `path` → `path+suffix` by streaming it through `binary`.

    The compressor runs in stream mode (`-c`: read stdin, write stdout) so we
    can pump the source in chunks and report progress by bytes consumed — a log
    line every 10% (readable in cron logs) plus an inline bar on a terminal.
    stdout is wired straight to the output file (no pipe → no back-pressure);
    stderr goes to a temp file (avoids a stderr-pipe deadlock on chatty tools).
    On a clean exit the source is removed here (stream mode has no -f/--rm).
    """
    from .progress import Progress  # local import: avoids a hard cycle at import

    cmd = [binary, "-c"]
    if level is not None:
        cmd.append(f"-{level}")
    cmd.extend(extra_args)

    out = path.with_suffix(path.suffix + suffix)
    total = path.stat().st_size
    prog = Progress("Compressing", total_bytes=total, label=path.name, log=log)

    log.debug("Running (stream): %s < %s > %s", " ".join(cmd), path.name, out.name)
    import tempfile
    try:
        with open(path, "rb") as fin, open(out, "wb") as fout, \
                tempfile.TemporaryFile() as errf:
            try:
                proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                                        stdout=fout, stderr=errf)
            except FileNotFoundError:
                raise RuntimeError(
                    f"{binary} not found. Install it (apt install {binary}) "
                    f"or change compression.method."
                )
            chunk = 4 * 1024 * 1024
            try:
                while True:
                    data = fin.read(chunk)
                    if not data:
                        break
                    proc.stdin.write(data)
                    prog.advance(len(data))
            except BrokenPipeError:
                # Compressor died early; wait() + stderr below surface the reason.
                pass
            finally:
                try:
                    proc.stdin.close()
                except OSError:
                    pass
            rc = proc.wait()
            errf.seek(0)
            err = errf.read().decode("utf-8", "replace").strip()
    except FileNotFoundError:
        # open(path) failing — original vanished under us.
        raise RuntimeError(f"{binary}: source {path} disappeared before compression")

    if rc != 0:
        prog.done(success=False)
        try:
            out.unlink()
        except OSError:
            pass
        raise RuntimeError(f"{binary} failed (rc={rc}): {err or 'no stderr'}")

    prog.done()
    if not out.exists():
        raise RuntimeError(f"{binary} succeeded but output {out} not found")
    # Stream mode leaves the source in place — remove it now that we're sure the
    # compressed output exists and the tool exited cleanly.
    try:
        path.unlink()
    except OSError as exc:
        log.warning("Compressed OK but could not remove source %s: %s", path, exc)

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
