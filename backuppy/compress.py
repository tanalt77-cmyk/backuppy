"""Compression methods for backup files."""
from __future__ import annotations

import errno
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

from .config import CompressionCfg

# method -> (binary, suffix, extra args). Extra args are for STREAM mode
# (compressor reads stdin, writes stdout — see _Compressor). File-mode flags
# like -f/--rm are intentionally absent: in stream mode there is no in-place
# file to force or remove.
_METHODS: dict[str, tuple[str, str, list[str]]] = {
    "gzip": ("gzip", ".gz", []),
    "bzip2": ("bzip2", ".bz2", []),
    "xz": ("xz", ".xz", ["-T", "0"]),     # -T 0 = all cores
    "zstd": ("zstd", ".zst", ["-T0"]),
}

# Below this much free space after a failed compressor run we report the
# failure as disk-full. The compressor's own message usually cannot say so:
# its stderr goes to a temp file on the same full disk.
_LOW_SPACE = 64 * 1024 * 1024


def _command(method: str, level: int | None) -> tuple[list[str], str]:
    binary, suffix, extra = _METHODS[method]
    cmd = [binary, "-c"]
    if level is not None:
        # zstd levels: 1-19 (normal), 20-22 (ultra) — the latter need --ultra.
        if binary == "zstd" and level >= 20:
            cmd.append("--ultra")
        cmd.append(f"-{level}")
    cmd.extend(extra)
    return cmd, suffix


def stream_suffix(cfg: CompressionCfg) -> str | None:
    """Suffix to stream-compress with, or None if streaming does not apply.

    Sources that build an archive themselves (FilesSource with archive_name)
    use this to pipe the tar straight into the compressor instead of writing an
    uncompressed tar first and compressing it afterwards.
    """
    method = cfg.method.lower()
    if not cfg.stream or method not in _METHODS:
        return None
    return _METHODS[method][1]


class _Compressor:
    """A compressor process reading stdin and writing straight to `out`.

    stdout is wired to the output file (no pipe → no back-pressure); stderr goes
    to a temp file (avoids a stderr-pipe deadlock on chatty tools). Write the
    uncompressed data to `stdin`, then call finish().
    """

    def __init__(self, cmd: list[str], out: Path):
        self.cmd = cmd
        self.out = out
        self._fout = open(out, "wb")
        self._errf = tempfile.TemporaryFile()
        try:
            self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                                         stdout=self._fout, stderr=self._errf)
        except FileNotFoundError:
            self._close_files()
            out.unlink(missing_ok=True)
            raise RuntimeError(
                f"{cmd[0]} not found. Install it (apt install {cmd[0]}) "
                f"or change compression.method."
            )
        self.stdin = self.proc.stdin

    def _close_files(self) -> None:
        for f in (self._fout, self._errf):
            try:
                f.close()
            except OSError:
                pass

    def finish(self) -> None:
        """Close stdin, wait for the compressor and raise if it failed.

        On failure the partial output is removed.
        """
        try:
            self.stdin.close()
        except OSError:
            pass  # compressor died early — wait() below tells why
        rc = self.proc.wait()
        try:
            self._errf.seek(0)
            err = self._errf.read().decode("utf-8", "replace").strip()
        except OSError:
            err = ""
        self._close_files()
        if rc == 0:
            if not self.out.exists():
                raise RuntimeError(f"{self.cmd[0]} succeeded but output {self.out} not found")
            return
        self.out.unlink(missing_ok=True)
        free = shutil.disk_usage(self.out.parent).free
        if "No space left" in err or free < _LOW_SPACE:
            raise OSError(errno.ENOSPC,
                          f"No space left on device: {self.cmd[0]} failed (rc={rc}) "
                          f"writing {self.out.name}; {free / 1024 / 1024:.0f} MB free "
                          f"in {self.out.parent}")
        raise RuntimeError(f"{self.cmd[0]} failed (rc={rc}): {err or 'no stderr'}")

    def abort(self) -> None:
        """Kill the compressor and remove partial output (error path)."""
        try:
            self.proc.kill()
        except OSError:
            pass
        try:
            self.stdin.close()
        except OSError:
            pass
        self.proc.wait()
        self._close_files()
        self.out.unlink(missing_ok=True)


def open_stream(out: Path, cfg: CompressionCfg) -> _Compressor:
    """Start a compressor that writes `out`; feed it via `.stdin`, then `.finish()`."""
    cmd, _suffix = _command(cfg.method.lower(), cfg.level)
    return _Compressor(cmd, out)


def compress_file(path: Path, cfg: CompressionCfg, log: logging.Logger) -> Path:
    """Compress a file in-place (well, alongside, then remove original).
    Returns the path of the compressed file."""
    method = cfg.method.lower()
    if method == "none":
        return path
    if method not in _METHODS:
        raise ValueError(f"Unknown compression method: {method}")

    log.info("Compressing with %s%s: %s",
             method,
             f" (level={cfg.level})" if cfg.level else "",
             path.name)
    cmd, suffix = _command(method, cfg.level)
    return _run(path, cmd, suffix, log)


def _run(path: Path, cmd: list[str], suffix: str, log: logging.Logger) -> Path:
    """Compress `path` → `path+suffix` by streaming it through the compressor.

    The source is pumped in chunks so progress can be reported by bytes
    consumed — a log line every 10% (readable in cron logs) plus an inline bar
    on a terminal. On a clean exit the source is removed here (stream mode has
    no -f/--rm).
    """
    from .progress import Progress  # local import: avoids a hard cycle at import

    out = path.with_suffix(path.suffix + suffix)
    total = path.stat().st_size
    prog = Progress("Compressing", total_bytes=total, label=path.name, log=log)

    log.debug("Running (stream): %s < %s > %s", " ".join(cmd), path.name, out.name)
    try:
        fin = open(path, "rb")
    except FileNotFoundError:
        # original vanished under us
        raise RuntimeError(f"{cmd[0]}: source {path} disappeared before compression")
    with fin:
        comp = _Compressor(cmd, out)
        chunk = 4 * 1024 * 1024
        try:
            while True:
                data = fin.read(chunk)
                if not data:
                    break
                comp.stdin.write(data)
                prog.advance(len(data))
        except BrokenPipeError:
            # Compressor died early; finish() surfaces the reason.
            pass
        try:
            comp.finish()
        except BaseException:
            prog.done(success=False)
            raise

    prog.done()
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
