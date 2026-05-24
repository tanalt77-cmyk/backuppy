"""Progress reporting: inline terminal bar + periodic log entries.

Two consumers:
  - The terminal user (stderr) — gets a single-line auto-updating bar.
  - The log file — gets a fresh line every N% (so cron logs are readable).

A single global flag controls whether progress is shown at all
(--no-progress disables it entirely for cron).
"""
from __future__ import annotations

import logging
import sys
import time


# Module-level flag — flipped by CLI when --no-progress is passed.
_DISABLED = False


def disable() -> None:
    """Globally disable all progress output (use for --no-progress)."""
    global _DISABLED
    _DISABLED = True


def is_disabled() -> bool:
    return _DISABLED


def _format_size(bytes_n: float) -> str:
    """1024 → '1.0 KB', 1500000 → '1.4 MB' etc."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(bytes_n) < 1024.0:
            return f"{bytes_n:.1f} {unit}"
        bytes_n /= 1024.0
    return f"{bytes_n:.1f} PB"


def _format_seconds(s: float) -> str:
    s = int(s)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m{s % 60:02d}s"
    return f"{s // 3600}h{(s % 3600) // 60:02d}m"


class Progress:
    """Track progress through a known-size byte stream.

    Usage:
        p = Progress("Uploading", total_bytes=12_000_000_000,
                     label="myfile.zst", log=log)
        for chunk in data:
            p.advance(len(chunk))
        p.done()
    """

    BAR_WIDTH = 30
    LOG_EVERY_PCT = 10              # log line every 10%
    TERMINAL_THROTTLE_SEC = 0.2     # refresh terminal at most 5×/sec

    def __init__(self, action: str, total_bytes: int,
                 label: str = "", log: logging.Logger | None = None,
                 tty_stream=None):
        self.action = action          # "Compressing", "Uploading", "Verifying"
        self.label = label            # filename
        self.total = max(1, total_bytes)
        self.done_bytes = 0
        self.start_time = time.monotonic()
        self.log = log
        self.tty = tty_stream if tty_stream is not None else sys.stderr
        self._tty_supports = (
            not _DISABLED
            and hasattr(self.tty, "isatty")
            and self.tty.isatty()
        )
        self._last_tty_update = 0.0
        self._next_log_pct = self.LOG_EVERY_PCT  # log first at 10%

    def advance(self, n: int) -> None:
        """Mark n more bytes as done."""
        if _DISABLED:
            return
        self.done_bytes += n
        self._maybe_emit()

    def set(self, done_bytes: int) -> None:
        """Set absolute progress (for sources that report position, not delta)."""
        if _DISABLED:
            return
        self.done_bytes = done_bytes
        self._maybe_emit()

    def _maybe_emit(self) -> None:
        pct = (self.done_bytes / self.total) * 100
        now = time.monotonic()

        # Terminal: refresh inline (throttled)
        if self._tty_supports and (now - self._last_tty_update) > self.TERMINAL_THROTTLE_SEC:
            self._draw_bar(pct, now)
            self._last_tty_update = now

        # Log: only when crossing 10%-marks
        if self.log and pct >= self._next_log_pct:
            elapsed = now - self.start_time
            rate = self.done_bytes / elapsed if elapsed > 0 else 0
            self.log.info(
                "%s %s: %d%% (%s / %s) %s/s",
                self.action,
                self.label,
                int(pct),
                _format_size(self.done_bytes),
                _format_size(self.total),
                _format_size(rate),
            )
            # Move to next 10% marker (catch up if we jumped by > 10)
            while self._next_log_pct <= pct:
                self._next_log_pct += self.LOG_EVERY_PCT

    def _draw_bar(self, pct: float, now: float) -> None:
        filled = int(self.BAR_WIDTH * pct / 100)
        bar = "█" * filled + "░" * (self.BAR_WIDTH - filled)
        elapsed = now - self.start_time
        rate = self.done_bytes / elapsed if elapsed > 0 else 0
        eta = (self.total - self.done_bytes) / rate if rate > 0 else 0
        rate_str = _format_size(rate) + "/s"
        line = (
            f"\r  {self.action} {self.label}: "
            f"[{bar}] {pct:5.1f}%  "
            f"{_format_size(self.done_bytes)} / {_format_size(self.total)}  "
            f"{rate_str}  ETA {_format_seconds(eta)}"
        )
        # Truncate to terminal width if known
        try:
            import shutil
            w = shutil.get_terminal_size((120, 24)).columns
            line = line[: w - 1]
        except Exception:
            pass
        self.tty.write(line)
        self.tty.flush()

    def done(self, success: bool = True) -> None:
        if _DISABLED:
            return
        now = time.monotonic()
        elapsed = now - self.start_time
        rate = self.done_bytes / elapsed if elapsed > 0 else 0

        # Clear inline bar (terminal only)
        if self._tty_supports:
            self.tty.write("\r" + " " * 120 + "\r")
            self.tty.flush()

        # Final log line
        if self.log and success:
            self.log.info(
                "%s %s: 100%% done (%s in %s, avg %s/s)",
                self.action, self.label,
                _format_size(self.done_bytes),
                _format_seconds(elapsed),
                _format_size(rate),
            )


class ProgressFile:
    """Wrap a file object so reading advances a Progress instance.

    Used to add progress to libraries that take a file-like object
    (boto3 upload_fileobj, requests with data=stream, paramiko, etc).
    """

    def __init__(self, fileobj, progress: Progress):
        self._f = fileobj
        self._p = progress

    def read(self, size: int = -1) -> bytes:
        data = self._f.read(size)
        if data:
            self._p.advance(len(data))
        return data

    def __iter__(self):
        return self

    def __next__(self):
        chunk = self.read(64 * 1024)
        if not chunk:
            raise StopIteration
        return chunk

    def __getattr__(self, name):
        return getattr(self._f, name)


def progress_callback(progress: Progress):
    """Return a callable that takes (bytes_done_delta).

    Suitable for boto3 Callback parameter, where boto calls it with bytes done."""
    def cb(bytes_done: int) -> None:
        progress.advance(bytes_done)
    return cb
