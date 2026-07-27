"""Core orchestration: triggers → sources → process → destinations.

Pipeline for one model:

  1. Run hooks.before
  2. For each trigger in cfg.triggers: run it. (May write files anywhere user told it to.)
  3. For each source in cfg.sources: pick up matching files into a temporary work_dir.
  4. For each artifact in work_dir: compress → encrypt → split.
  5. For each destination: upload each artifact, verify, rotate.
  6. Run hooks.on_success or hooks.on_failure
  7. Run hooks.after
  8. Send notifications

Triggers and sources are decoupled: a trigger writes files somewhere on disk,
and sources are responsible for finding and picking them up. This lets users:
- Use just sources (no triggers) for "files already on disk"
- Use just triggers (no sources) if they want to fire-and-forget
  [actually: at least one source is required, or pipeline does nothing]
- Combine multiple triggers + multiple sources freely
"""
from __future__ import annotations

import datetime as dt
import errno
import glob as _glob
import json
import logging
import logging.handlers
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

from .config import Config, LogCfg
from .compress import compress_file
from .encrypt import encrypt_file
from .splitter import split_file
from .notify import notify_all
from .triggers import build_trigger
from .sources import build_source
from .storages import (
    BaseStorage, LocalStorage,
    WebDAVStorage, S3Storage, SFTPStorage,
    DropboxStorage, GCSStorage, AzureBlobStorage,
)


# ============================================================================
# Logging
# ============================================================================

def setup_logger(cfg: LogCfg) -> logging.Logger:
    log = logging.getLogger("backuppy")
    log.setLevel(getattr(logging, cfg.level.upper(), logging.INFO))
    log.handlers.clear()
    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    log.addHandler(sh)
    try:
        Path(cfg.file).parent.mkdir(parents=True, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(
            cfg.file, maxBytes=cfg.max_bytes, backupCount=cfg.backup_count
        )
        fh.setFormatter(fmt)
        log.addHandler(fh)
    except PermissionError as e:
        log.warning("Could not open log file %s: %s", cfg.file, e)
    return log


# ============================================================================
# Hooks
# ============================================================================

def run_hooks(stage: str, commands: list[str], log: logging.Logger) -> None:
    if not commands:
        return
    log.info("Hooks: %s — %d command(s)", stage, len(commands))
    for cmd in commands:
        hard = cmd.startswith("!")
        actual = cmd[1:] if hard else cmd
        log.info("  $ %s", actual)
        res = subprocess.run(actual, shell=True, capture_output=True, text=True)
        if res.stdout.strip():
            log.info("    stdout: %s", res.stdout.strip()[:500])
        if res.returncode != 0:
            msg = f"hook failed (exit={res.returncode}): {actual}\n{res.stderr.strip()}"
            if hard:
                raise RuntimeError(msg)
            log.warning("    %s", msg)


# ============================================================================
# Destinations factory
# ============================================================================

def build_destinations(cfg: Config, log: logging.Logger) -> list[BaseStorage]:
    """Build ALL enabled destinations (excluding local — handled separately).

    Every destination's path/prefix is suffixed with /<model_name>/ — this
    keeps files of different models separated in the same root storage.

    Each cfg is shallow-copied so we don't mutate the user's parsed config.
    """
    import copy
    out: list[BaseStorage] = []

    def _with_model(path_field: str, cfg_obj):
        """Return a shallow copy of cfg_obj with path_field suffixed by /<name>"""
        c = copy.copy(cfg_obj)
        original = getattr(c, path_field, "")
        # Normalize: strip trailing slash, append model name
        sep = "/"
        new_value = original.rstrip("/").rstrip("\\") + sep + cfg.name
        setattr(c, path_field, new_value)
        return c

    if cfg.webdav.enabled:
        out.append(WebDAVStorage(_with_model("remote_path", cfg.webdav), log))
    if cfg.s3.enabled:
        out.append(S3Storage(_with_model("prefix", cfg.s3), log))
    if cfg.sftp.enabled:
        out.append(SFTPStorage(_with_model("remote_path", cfg.sftp), log))
    if cfg.dropbox.enabled:
        out.append(DropboxStorage(_with_model("remote_path", cfg.dropbox), log))
    if cfg.gcs.enabled:
        out.append(GCSStorage(_with_model("prefix", cfg.gcs), log))
    if cfg.azure.enabled:
        out.append(AzureBlobStorage(_with_model("prefix", cfg.azure), log))
    return out


def build_local(cfg: Config, log: logging.Logger):
    """Build LocalStorage with model name appended to path."""
    if not cfg.local.enabled:
        return None
    import copy
    from pathlib import Path
    local_cfg = copy.copy(cfg.local)
    local_cfg.path = str(Path(local_cfg.path) / cfg.name)
    return LocalStorage(local_cfg, log)


def keep_last_for(storage: BaseStorage, cfg: Config) -> int:
    return {
        "webdav": cfg.webdav.keep_last,
        "s3": cfg.s3.keep_last,
        "sftp": cfg.sftp.keep_last,
        "dropbox": cfg.dropbox.keep_last,
        "gcs": cfg.gcs.keep_last,
        "azure": cfg.azure.keep_last,
    }.get(storage.name, 10)


# ============================================================================
# Commands
# ============================================================================


def _humanize_error(exc: BaseException) -> str:
    """Turn an exception chain into a short, operator-friendly summary.

    Errors during backup are typically one of: network/DNS, auth, disk full,
    permissions, missing files. The raw traceback for those is ~80 lines of
    library internals that hide the actual cause from non-developers. Walk
    the cause chain to the root, classify common cases, fall back to the
    exception class+message for unknown ones.
    """
    root = exc
    while root.__cause__ is not None or root.__context__ is not None:
        root = root.__cause__ or root.__context__
    msg = str(root) or root.__class__.__name__
    cls = root.__class__.__name__

    # Network/DNS — usually wrapped in urllib3/requests/botocore exceptions.
    # Reach into the original to surface a single actionable line.
    chain_text = " | ".join(str(e) for e in _exception_chain(exc))
    if "Name or service not known" in chain_text or "NameResolutionError" in cls:
        host = _extract_host(chain_text)
        if host:
            return f"DNS lookup failed for {host} — check /etc/resolv.conf or the URL"
        return "DNS lookup failed — check /etc/resolv.conf or the destination URL"
    if "Connection refused" in chain_text:
        return f"Connection refused — {msg}"
    if "Connection timed out" in chain_text or "TimeoutError" in cls:
        return f"Connection timed out — {msg}"
    if "Certificate" in chain_text or "SSL" in chain_text or "TLS" in chain_text:
        return f"TLS/certificate problem — {msg}"
    if "No space left on device" in chain_text:
        return "Disk full on the agent — free up /var/tmp/backuppy or set tmp_dir to a larger disk"
    if "Permission denied" in chain_text:
        return f"Permission denied — {msg}"
    if "401" in chain_text or "403" in chain_text or "Unauthorized" in chain_text:
        return f"Authentication failed — check storage credentials ({msg})"
    if "Device or resource busy" in chain_text:
        return f"File held open by another process — {msg}"

    return f"{cls}: {msg}"


def _exception_chain(exc: BaseException) -> list[BaseException]:
    """Walk __cause__/__context__ producing the root-to-leaf list."""
    out: list[BaseException] = []
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        out.append(cur)
        cur = cur.__cause__ or cur.__context__
    return out


def _extract_host(text: str) -> str | None:
    """Pull a host out of a ConnectionPool message like
    \"HTTPSConnectionPool(host='data.pixophone.com', port=443)\"."""
    import re as _re
    m = _re.search(r"host='([^']+)'", text)
    if m:
        return m.group(1)
    m = _re.search(r"Failed to resolve '([^']+)'", text)
    if m:
        return m.group(1)
    return None


# --- Source availability / completeness guards -----------------------------
#
# Sources and the local destination often live on the SAME Windows host (a CIFS
# share). When that host reboots at night, the mount goes dead and every read
# fails with EHOSTDOWN/112. Two things then go wrong:
#   1) the run dies mid-way (observed: OSError 112 in build_local), and
#   2) worse — if only PART of the source is readable, FilesSource just logs a
#      warning and backs up whatever it could see. That "successful" partial
#      backup then ROTATES OUT the good older copies, which are now physically
#      deleted on B2. A short outage could therefore destroy real backups.
#
# So before doing anything we (a) wait for the mounts to come back, and (b)
# compare the source size against the previous successful run and refuse to
# continue if it shrank suspiciously.

_STATE_DIR = Path("/var/lib/backuppy/state")

# Abort if the source is smaller than this fraction of the last good run.
_MIN_SOURCE_RATIO = float(os.environ.get("BACKUPPY_MIN_SOURCE_RATIO", "0.7"))
# How long to wait for a dead mount to come back (host reboot), and how often.
_MOUNT_WAIT_SECONDS = int(os.environ.get("BACKUPPY_MOUNT_WAIT", "600"))
_MOUNT_RETRY_INTERVAL = 30

# errnos that mean "the server hosting this mount is unreachable right now"
_HOST_DOWN_ERRNOS = {errno.EHOSTDOWN, errno.ENOTCONN, errno.EHOSTUNREACH,
                     errno.ETIMEDOUT, errno.ENETUNREACH, errno.ESTALE}


def _glob_root(pattern: str) -> str:
    """The literal part of a path/pattern, up to the first wildcard.

    Deliberately does NOT fall back to the parent directory when the path does
    not resolve: a dead CIFS mount fails is_dir() exactly like a missing path,
    and probing its parent (/mnt, which is always alive) would hide the very
    outage we are trying to detect."""
    parts = []
    for seg in Path(pattern).parts:
        if any(ch in seg for ch in "*?["):
            break
        parts.append(seg)
    return str(Path(*parts)) if parts else "/"


def _probe(path: str) -> None:
    """Touch a path so a dead CIFS mount raises instead of looking empty."""
    p = Path(path)
    if p.is_dir():
        next(iter(os.scandir(path)), None)   # forces a real server round-trip
    else:
        p.stat()


def _files_source_patterns(cfg: Config) -> list[str]:
    out: list[str] = []
    for s in cfg.sources or []:
        if (s.get("type") or "") == "files":
            out.extend(s.get("paths") or [])
    return out


def _classify_path(path: str) -> tuple[str, str]:
    """('ok'|'dead'|'missing', detail) for one source/destination path.

    A missing sub-directory on a LIVE share is not an outage: FilesSource has
    always just warned about it and backed up the rest. Only an unreachable
    server should hold up the run. So when a path is absent we walk up to its
    nearest existing ancestor and probe that: if the ancestor answers, the mount
    is alive and the path is merely gone ('missing'); if the probe fails with a
    host-down errno, the server really is unreachable ('dead')."""
    try:
        _probe(path)
        return ("ok", "")
    except OSError as e:
        if e.errno in _HOST_DOWN_ERRNOS or e.errno == errno.EIO:
            return ("dead", f"{path} ({e.strerror or e.errno})")
        if e.errno != errno.ENOENT:
            return ("dead", f"{path} ({e})")
    # ENOENT — is the mount underneath it still alive?
    p = Path(path)
    for anc in p.parents:
        try:
            _probe(str(anc))
            return ("missing", f"{path} (немає на живій шарі {anc})")
        except OSError as e:
            if e.errno in _HOST_DOWN_ERRNOS or e.errno == errno.EIO:
                return ("dead", f"{path} (шара {anc}: {e.strerror or e.errno})")
            if e.errno == errno.ENOENT:
                continue
            return ("dead", f"{path} ({e})")
    return ("dead", f"{path} (недоступний)")


def _wait_for_paths(paths: list[str], log: logging.Logger) -> None:
    """Block until every path's HOST is reachable, or raise after
    _MOUNT_WAIT_SECONDS. Paths that are simply absent on a live share are
    reported as warnings and do NOT hold up (or fail) the run — the shrink
    guard below is what catches "too much data went missing"."""
    deadline = time.monotonic() + _MOUNT_WAIT_SECONDS
    warned = False
    while True:
        dead: list[str] = []
        missing: list[str] = []
        for p in paths:
            state, detail = _classify_path(_glob_root(p))
            if state == "dead":
                dead.append(detail)
            elif state == "missing":
                missing.append(detail)
        if not dead:
            if warned:
                log.info("Source/destination mounts are back — continuing")
            if missing and not any(
                _classify_path(_glob_root(p))[0] == "ok" for p in paths
            ):
                # Nothing at all is there. A single renamed folder is routine,
                # but ALL paths vanishing means the share was unmounted (or the
                # data really is gone) — never quietly back that up.
                raise RuntimeError(
                    "Backup aborted: none of the source paths exist — "
                    + "; ".join(missing)
                    + ". The share is probably not mounted. NOTHING was "
                      "uploaded or rotated, so existing backups are untouched."
                )
            if missing:
                log.warning("Source path(s) absent (share is alive, continuing): %s",
                            "; ".join(missing))
            return
        if time.monotonic() >= deadline:
            raise RuntimeError(
                "Backup aborted: source/destination host is unreachable — "
                + "; ".join(dead)
                + f". Waited {_MOUNT_WAIT_SECONDS}s. NOTHING was uploaded or "
                  "rotated, so existing backups are untouched."
            )
        if not warned:
            log.warning("Waiting for unreachable host(s): %s — retrying for up "
                        "to %ds before giving up", "; ".join(dead),
                        _MOUNT_WAIT_SECONDS)
            warned = True
        time.sleep(_MOUNT_RETRY_INTERVAL)


def _measure_sources(patterns: list[str]) -> tuple[int, int]:
    """(file_count, total_bytes) currently visible under the source patterns."""
    n = 0
    total = 0
    for pat in patterns:
        for m in _glob.glob(pat, recursive=True):
            p = Path(m)
            try:
                if p.is_dir():
                    for f in p.rglob("*"):
                        if f.is_file():
                            n += 1
                            total += f.stat().st_size
                elif p.is_file():
                    n += 1
                    total += p.stat().st_size
            except OSError:
                continue
    return n, total


def _state_file(name: str) -> Path:
    return _STATE_DIR / f"{name}.json"


def _load_source_state(name: str) -> dict:
    try:
        return json.loads(_state_file(name).read_text())
    except Exception:  # noqa: BLE001 — missing/corrupt state just means "no baseline"
        return {}


def _save_source_state(name: str, files: int, total: int,
                       log: logging.Logger, *, baseline: bool = True) -> None:
    """Persist source measurements.

    baseline=True  → record the accepted baseline (only after a good run).
    baseline=False → only update 'last_seen' (every run, even aborted ones), so
                     the shrink guard can tell a STABLE shrink (real removal)
                     from a FLAPPING one (partial read).
    """
    try:
        _STATE_DIR.mkdir(parents=True, exist_ok=True)
        prev = _load_source_state(name)
        data = dict(prev)
        data["last_seen"] = total
        data["last_seen_at"] = dt.datetime.now().isoformat(timespec="seconds")
        if baseline:
            data["files"] = files
            data["bytes"] = total
            data["at"] = data["last_seen_at"]
        _state_file(name).write_text(json.dumps(data))
    except Exception as e:  # noqa: BLE001 — never fail a good backup over this
        log.debug("could not save source state: %s", e)


def preflight_sources(cfg: Config, log: logging.Logger) -> tuple[int, int]:
    """Verify sources (and the local dest) are reachable and look complete.

    Returns (file_count, total_bytes) so the caller can record them as the new
    baseline AFTER the run succeeds. Raises RuntimeError — before anything is
    uploaded or rotated — if a mount stays dead or the source shrank sharply.
    """
    patterns = _files_source_patterns(cfg)
    has_triggers = bool(getattr(cfg, "triggers", None))

    # What must be reachable BEFORE the run. For DB models the source is a dump
    # produced during the run (its staging dir may not exist yet), so we only
    # require the local destination share to be alive; for files models we also
    # require the source paths' host.
    to_check = []
    if not has_triggers:
        to_check.extend(patterns)
    if getattr(cfg, "local", None) is not None and getattr(cfg.local, "enabled", False):
        if cfg.local.path:
            to_check.append(str(cfg.local.path))
    if to_check:
        _wait_for_paths(to_check, log)

    if not patterns:
        return (0, 0)

    # Models with triggers (MSSQL/Postgres/MySQL) DUMP their source during the
    # run — before that dump exists the source dir is empty. Measuring here, at
    # the start, would always see 0 bytes and trip the shrink guard. So for
    # trigger-driven models we only checked reachability above; the size/shrink
    # guard is meaningless and is skipped.
    if has_triggers:
        log.info("Source pre-flight: reachability OK (DB model — source is "
                 "produced by triggers, size guard skipped)")
        return (0, 0)

    files, total = _measure_sources(patterns)
    prev = _load_source_state(cfg.name)
    prev_bytes = int(prev.get("bytes") or 0)
    last_seen = int(prev.get("last_seen") or 0)
    # Record what we saw THIS run (not as a new baseline) so the next run can
    # judge stability, even if we abort below.
    _save_source_state(cfg.name, files, total, log, baseline=False)

    if prev_bytes and total < prev_bytes * _MIN_SOURCE_RATIO:
        # The source dropped sharply. A partial read fluctuates run-to-run; a
        # real removal (folders deleted/renamed) is stable. If this run's size
        # matches the previous run's within 10%, treat it as the new normal and
        # continue — otherwise it looks like a flapping/partial read and we stop.
        tol = max(last_seen * 0.10, 50 * 1024 * 1024)
        stable = last_seen and abs(total - last_seen) <= tol
        if not stable:
            raise RuntimeError(
                f"Backup aborted: source shrank to {total / 1024 / 1024:.0f} MB from "
                f"{prev_bytes / 1024 / 1024:.0f} MB last time "
                f"({100.0 * total / prev_bytes:.0f}% — under the "
                f"{_MIN_SOURCE_RATIO:.0%} threshold). This usually means the share "
                "is only partially readable. NOTHING was uploaded or rotated, so "
                "existing backups are untouched. If the drop is real (files were "
                "removed), just run it again — a second run at the same size is "
                "accepted as the new baseline. Or set BACKUPPY_MIN_SOURCE_RATIO."
            )
        log.warning(
            "Source is %.0f MB, well below the previous baseline of %.0f MB, but "
            "matches the last run (%.0f MB) — treating the smaller size as the new "
            "normal and continuing.", total / 1024 / 1024,
            prev_bytes / 1024 / 1024, last_seen / 1024 / 1024)
    log.info("Source pre-flight: %d file(s), %.2f MB%s",
             files, total / 1024 / 1024,
             f" (previous run: {prev_bytes / 1024 / 1024:.2f} MB)" if prev_bytes else "")
    return files, total


def cmd_run(cfg: Config, log: logging.Logger, dry_run: bool) -> int:
    from .notify import WarningCollector
    host = socket.gethostname()
    started = dt.datetime.now()
    log.info("=== backuppy '%s' start on %s ===", cfg.name, host)

    if not cfg.sources:
        log.error("Model has no 'sources:' — nothing to back up. "
                  "Add at least one source: { type: files, paths: [...] }")
        return 2

    # Attach warning collector for this run only
    warn_collector = WarningCollector()
    warn_collector.setFormatter(logging.Formatter("%(message)s"))
    log.addHandler(warn_collector)

    # Verify the source/destination shares are alive and complete BEFORE
    # touching anything. build_local() below is exactly where a dead CIFS mount
    # used to blow up with OSError 112, and a half-readable share used to
    # produce a partial backup that then rotated out the good copies.
    src_files = src_bytes = 0
    if not dry_run:
        src_files, src_bytes = preflight_sources(cfg, log)

    triggers = [build_trigger(t, log) for t in cfg.triggers]
    sources = [build_source(s, log) for s in cfg.sources]
    remote_dests = build_destinations(cfg, log)
    local = build_local(cfg, log)

    # Log destinations summary so cron logs make it obvious where backups
    # were sent (or weren't, if a section is disabled).
    _log_destinations_summary(cfg, log)

    # If group_by_run is enabled, every destination is told to use a
    # per-run subdirectory named YYYYMMDD-HHMMSS. All artifacts of this
    # run land inside one subfolder, rotation runs per-directory.
    run_subdir: str | None = None
    if cfg.group_by_run:
        run_subdir = started.strftime("%Y%m%d-%H%M%S")
        log.info("group_by_run: enabled — using subdir '%s'", run_subdir)
        if local:
            local.set_run_subdir(run_subdir)
        for d in remote_dests:
            d.set_run_subdir(run_subdir)

    if dry_run:
        log.info("[DRY-RUN] no changes will be made")
        for t in triggers:
            log.info("  trigger: %s", t.type)
        for s in sources:
            log.info("  source: %s paths=%s", s.type, s.paths)
        log.info("  compression: %s", cfg.compression.method)
        if cfg.encryption.enabled:
            log.info("  encryption: %s", cfg.encryption.method)
        if local:
            log.info("  local: %s (keep=%d)", cfg.local.path, cfg.local.keep_last)
        for d in remote_dests:
            log.info("  remote: %s (keep=%d)", d.name, keep_last_for(d, cfg))
        return 0

    try:
        run_hooks("before", cfg.hooks.before, log)
    except RuntimeError as e:
        log.error("Aborting: %s", e)
        return 1

    # Resolve where temporary files should go. If tmp_dir is configured,
    # ensure it exists; otherwise Python's default (TMPDIR or /tmp) is used.
    tmp_parent: str | None = None
    if cfg.tmp_dir:
        Path(cfg.tmp_dir).mkdir(parents=True, exist_ok=True)
        tmp_parent = cfg.tmp_dir
        log.debug("Using tmp_dir: %s", tmp_parent)

    # Sweep stale work dirs left behind by previous runs that were killed
    # before TemporaryDirectory could clean up (OOM, SIGKILL, power loss,
    # host reboot). Without this they accumulate silently and eventually
    # fill the disk — observed 57G of stale backuppy-* dirs in the wild.
    #
    # Two safeguards keep us from disturbing a CONCURRENT run that shares
    # the same tmp_dir (e.g. weekly cron firing while daily still packing):
    #   - we only touch dirs matching our own prefix ("backuppy-*"), so
    #     foreign files in the same parent are left alone;
    #   - we skip dirs whose newest entry was modified less than STALE_AGE
    #     seconds ago, on the assumption that a live run keeps writing to
    #     its tar. An hour is conservatively above the longest plausible
    #     "idle but alive" gap (e.g. a slow upload after compression).
    # TemporaryDirectory itself still handles the normal cases — this is
    # a recovery net for processes that were killed outright.
    STALE_AGE = 3600  # seconds
    sweep_parent = tmp_parent or tempfile.gettempdir()
    now = dt.datetime.now().timestamp()
    try:
        for leftover in Path(sweep_parent).glob("backuppy-*"):
            if not leftover.is_dir():
                continue
            try:
                # Newest mtime across the dir + its contents. Bare dir
                # alone might be misleading (untouched after creation
                # while children change), and tar writers update file
                # mtime as they append.
                mtimes = [leftover.stat().st_mtime]
                mtimes.extend(p.stat().st_mtime for p in leftover.iterdir())
                age = now - max(mtimes)
            except OSError:
                continue
            if age < STALE_AGE:
                log.debug("Skipping recent work dir (age %.0fs): %s",
                          age, leftover)
                continue
            try:
                shutil.rmtree(leftover)
                log.info("Removed stale work dir: %s (age %.0fs)",
                         leftover, age)
            except OSError as exc:
                log.warning("Could not remove stale work dir %s: %s",
                            leftover, exc)
    except OSError:
        pass

    try:
        with tempfile.TemporaryDirectory(prefix="backuppy-", dir=tmp_parent) as tmp:
            work = Path(tmp)

            # 1. Run triggers
            for t in triggers:
                log.info("--- Trigger: %s ---", t.type)
                t.run()

            # 2. Pick up files via sources
            artifacts: list[Path] = []
            for s in sources:
                artifacts.extend(s.pickup(work, cfg.name))

            if not artifacts:
                # A run that uploaded NOTHING has failed — "backed up nothing"
                # is not a success-with-warning. Raise so the failure path fires
                # (non-zero exit + 'failure' notification), instead of silently
                # finishing as Done. This is what makes disk-full / all-sources-
                # skipped runs actually page the operator.
                _joined = "; ".join(warn_collector.messages)
                if "Errno 28" in _joined or "No space left" in _joined:
                    _why = ("No space left on device while staging the backup. "
                            "The temp/staging directory is too small — point "
                            "tmp_dir at a bigger disk, or enable 'temporary files "
                            "on the model's SMB share'.")
                elif _joined:
                    _why = f"All sources were skipped: {_joined}"
                else:
                    _why = "No source matched any files."
                raise RuntimeError(
                    f"No artifacts produced — nothing to upload. {_why}")

            # Even if SOME artifacts were produced, a disk-full during pickup
            # means the set is incomplete — fail rather than silently reporting
            # partial success (a half-done backup is worse than a known failure).
            _disk = "; ".join(m for m in warn_collector.messages
                              if "Errno 28" in m or "No space left" in m)
            if _disk:
                raise RuntimeError(
                    "Backup incomplete — no space left on device during "
                    f"staging: {_disk}. Point tmp_dir at a bigger disk or "
                    "enable 'temporary files on the model's SMB share'.")

            # 3. Process each artifact
            for art in artifacts:
                art = compress_file(art, cfg.compression, log)
                art = encrypt_file(art, cfg.encryption, log)
                parts = split_file(art, cfg.splitter, log)
                for part in parts:
                    # local first (acts as a staging area too)
                    if local:
                        local_dest = local.store(part)
                    else:
                        local_dest = part
                    for dest in remote_dests:
                        _upload_with_verify(dest, local_dest, cfg, log)

            # 4. Rotation
            _rotate_all(cfg, sources, local, remote_dests, log)

            # Baseline for the next run's shrink check — recorded only now,
            # i.e. only after a complete, verified, uploaded backup.
            if src_bytes:
                _save_source_state(cfg.name, src_files, src_bytes, log)

    except Exception as e:
        # Operator-facing summary on one line — the actual cause, not the
        # urllib3/requests/botocore wrapping chain.
        summary = _humanize_error(e)
        log.error("FAILED: %s", summary)
        # Full traceback to DEBUG so it's still in the file log for forensics
        # without polluting the operator's view of what went wrong.
        tb = traceback.format_exc()
        log.debug("Full traceback:\n%s", tb)
        run_hooks("on_failure", cfg.hooks.on_failure, log)
        run_hooks("after", cfg.hooks.after, log)
        log.removeHandler(warn_collector)
        body = (
            f"Backup failed at {started:%Y-%m-%d %H:%M:%S}\n\n"
            f"{summary}\n\n"
            f"Full traceback (for debugging):\n{tb}"
        )
        if warn_collector.messages:
            body += (
                f"\n\nWarnings collected before failure "
                f"({len(warn_collector.messages)}):\n  - "
                + "\n  - ".join(warn_collector.messages)
            )
        notify_all(cfg.email, cfg.telegram, "failure",
                   f"[backuppy] FAIL {cfg.name} @ {host}",
                   body, log)
        return 1

    elapsed = (dt.datetime.now() - started).total_seconds()
    log.info("=== Done in %.1fs ===", elapsed)

    run_hooks("on_success", cfg.hooks.on_success, log)
    run_hooks("after", cfg.hooks.after, log)
    log.removeHandler(warn_collector)

    if warn_collector.messages:
        outcome = "warning"
        subject = f"[backuppy] WARN {cfg.name} @ {host}"
        body = (
            f"Backup completed with warnings at "
            f"{started:%Y-%m-%d %H:%M:%S}\n"
            f"Total time: {elapsed:.1f}s\n\n"
            f"Warnings ({len(warn_collector.messages)}):\n  - "
            + "\n  - ".join(warn_collector.messages)
        )
    else:
        outcome = "success"
        subject = f"[backuppy] OK {cfg.name} @ {host}"
        body = f"Backup finished successfully in {elapsed:.1f}s."

    notify_all(cfg.email, cfg.telegram, outcome, subject, body, log)
    return 0


def _upload_with_verify(storage: BaseStorage, local_dest: Path,
                        cfg: Config, log: logging.Logger) -> None:
    storage.upload(local_dest)
    if cfg.verify.enabled:
        ok = storage.verify(local_dest, local_dest.name,
                            cfg.verify.method, log)
        if not ok:
            raise RuntimeError(
                f"Verify failed: {storage.name} upload of {local_dest.name}"
            )
        log.info("  %s verify: OK", storage.name)


def _log_destinations_summary(cfg: "Config", log: logging.Logger) -> None:
    """Print a per-destination summary showing enabled/disabled status.

    Helps catch the common 'I forgot to enable S3' situation: if a section
    exists in the YAML but enabled=false, we want the user to SEE that.
    """
    rows: list[tuple[str, bool, str]] = [
        ("local",   cfg.local.enabled,   cfg.local.path),
        ("webdav",  cfg.webdav.enabled,  cfg.webdav.remote_path),
        ("s3",      cfg.s3.enabled,      f"{cfg.s3.bucket}/{cfg.s3.prefix}"),
        ("sftp",    cfg.sftp.enabled,    cfg.sftp.remote_path),
        ("dropbox", cfg.dropbox.enabled, cfg.dropbox.remote_path),
        ("gcs",     cfg.gcs.enabled,     f"{cfg.gcs.bucket}/{cfg.gcs.prefix}"),
        ("azure",   cfg.azure.enabled,   f"{cfg.azure.container}/{cfg.azure.prefix}"),
    ]
    enabled = [r for r in rows if r[1]]
    disabled = [r[0] for r in rows if not r[1]]

    log.info("Destinations:")
    if enabled:
        for name, _, path in enabled:
            log.info("  ✓ %-8s enabled  → %s", name, path)
    if disabled:
        log.info("  · disabled: %s", ", ".join(disabled))
    if not enabled:
        log.warning("No destinations enabled — backups will only stay in tmp_dir.")


def _rotate_all(cfg: Config, sources, local, remotes, log: logging.Logger) -> None:
    """Rotate backups. Two modes:

    1) group_by_run = True: rotate by run-directory.
       keep_last keeps that many run-subdirs.
    2) group_by_run = False (default): rotate by filename prefix (per-file).
    """
    if cfg.group_by_run:
        # Per-directory rotation
        if local:
            local.rotate_run_dirs(cfg.local.keep_last, log)
        for s in remotes:
            keep = keep_last_for(s, cfg)
            s.rotate_run_dirs(keep, log)
        return

    # Per-file rotation (original behavior)
    prefixes: list[str] = []
    for s in sources:
        prefixes.extend(s.prefixes(cfg.name))

    if not prefixes:
        log.debug("No archive-name prefixes; rotation skipped")
        return

    if local:
        for p in prefixes:
            local.rotate(p, cfg.local.keep_last, log)

    for s in remotes:
        keep = keep_last_for(s, cfg)
        for p in prefixes:
            s.rotate(p, keep, log)


def cmd_list(cfg: Config, log: logging.Logger) -> int:
    if cfg.local.enabled:
        log.info("Local backups in %s:", cfg.local.path)
        local = build_local(cfg, log)
        for f in sorted(local.list_files(), key=lambda x: x["name"]):
            mb = f["size"] / 1024 / 1024
            print(f"  {f['name']}  ({mb:.2f} MB)")

    for d in build_destinations(cfg, log):
        log.info("Backups in %s:", d.name)
        try:
            for f in sorted(d.list_files(), key=lambda x: x["name"]):
                mb = f["size"] / 1024 / 1024
                modified = f.get("modified", "")
                print(f"  {f['name']}  ({mb:.2f} MB, {modified})")
        except Exception as e:
            log.error("  %s: %s", d.name, e)
    return 0


def _dest_location(cfg: Config, name: str) -> str:
    """Human-readable '<model>/' folder location for a destination type."""
    def _pfx(p: str) -> str:
        return (p.rstrip("/") + "/") if p else ""
    if name == "s3":
        return f"{cfg.s3.bucket}/{_pfx(cfg.s3.prefix)}{cfg.name}"
    if name == "gcs":
        return f"{cfg.gcs.bucket}/{_pfx(cfg.gcs.prefix)}{cfg.name}"
    if name == "azure":
        return f"{cfg.azure.container}/{_pfx(cfg.azure.prefix)}{cfg.name}"
    if name == "webdav":
        return f"{cfg.webdav.remote_path.rstrip('/')}/{cfg.name}"
    if name == "sftp":
        return f"{cfg.sftp.remote_path.rstrip('/')}/{cfg.name}"
    if name == "dropbox":
        return f"{cfg.dropbox.remote_path.rstrip('/')}/{cfg.name}"
    if name == "local":
        return str(Path(cfg.local.path) / cfg.name)
    return cfg.name


def model_usage(cfg: Config, log: logging.Logger) -> dict:
    """How much space this model's backups occupy on each destination.

    Each destination's folder is already scoped to /<model>/, so summing
    list_files() sizes gives exactly that model's footprint. 'backups' counts
    distinct top-level run-subdirs (one per backup when group_by_run is on).
    """
    out: dict = {"name": cfg.name, "destinations": [], "total_bytes": 0}

    def _measure(store, type_name: str) -> None:
        loc = _dest_location(cfg, type_name)
        try:
            files = store.list_files()
        except Exception as e:  # noqa: BLE001
            out["destinations"].append({"type": type_name, "location": loc,
                                        "error": str(e)[:200]})
            return
        total = sum(int(f.get("size", 0) or 0) for f in files)
        runs = {f["name"].split("/", 1)[0] for f in files if "/" in f["name"]}
        out["destinations"].append({
            "type": type_name, "location": loc,
            "bytes": total, "files": len(files), "backups": len(runs),
        })
        out["total_bytes"] += total

    if cfg.local.enabled:
        local = build_local(cfg, log)
        if local is not None:
            _measure(local, "local")
    for d in build_destinations(cfg, log):
        _measure(d, d.name)
    return out


_RUNDIR_RE = re.compile(r"^\d{8}-\d{6}$")


def model_storage_detail(cfg: Config, log: logging.Logger) -> dict:
    """Per-destination listing of a model's run-dirs (one per backup) + sizes.

    Lets the portal show exactly what's stored and how big each backup is, so
    an operator can delete specific old ones by hand.
    """
    out: dict = {"name": cfg.name, "destinations": []}

    def _detail(store, type_name: str) -> None:
        loc = _dest_location(cfg, type_name)
        try:
            files = store.list_files()
        except Exception as e:  # noqa: BLE001
            out["destinations"].append({"type": type_name, "location": loc,
                                        "error": str(e)[:200]})
            return
        runs: dict = {}
        loose = {"bytes": 0, "files": 0}
        for f in files:
            name = f["name"]
            size = int(f.get("size", 0) or 0)
            top = name.split("/", 1)[0] if "/" in name else None
            if top:
                r = runs.setdefault(top, {"bytes": 0, "files": 0})
                r["bytes"] += size
                r["files"] += 1
            else:
                loose["bytes"] += size
                loose["files"] += 1
        run_list = [{"name": k, "bytes": v["bytes"], "files": v["files"],
                     "is_run": bool(_RUNDIR_RE.match(k))}
                    for k, v in sorted(runs.items(), reverse=True)]
        out["destinations"].append({
            "type": type_name, "location": loc, "runs": run_list,
            "loose_files": loose["files"], "loose_bytes": loose["bytes"],
        })

    if cfg.local.enabled:
        local = build_local(cfg, log)
        if local is not None:
            _detail(local, "local")
    for d in build_destinations(cfg, log):
        _detail(d, d.name)
    return out


def delete_run_dir(cfg: Config, run_dir: str, dest_type: str | None,
                   log: logging.Logger, dry_run: bool = True) -> dict:
    """Delete one run-dir from one (or all) of a model's destinations.

    Safety: only names matching a backuppy run-dir (YYYYMMDD-HHMMSS) are
    accepted, and deletion is scoped to the model's own /<model>/ prefix —
    it can never reach sibling models or non-backuppy objects. Dry-run by
    default; pass dry_run=False to actually delete.
    """
    if not _RUNDIR_RE.match(run_dir):
        raise ValueError(
            f"refusing to delete '{run_dir}': not a backuppy run-dir "
            f"(expected YYYYMMDD-HHMMSS)")

    stores: list[tuple[str, object]] = []
    if cfg.local.enabled and dest_type in (None, "local"):
        local = build_local(cfg, log)
        if local is not None:
            stores.append(("local", local))
    for d in build_destinations(cfg, log):
        if dest_type in (None, d.name):
            stores.append((d.name, d))

    results = []
    for name, store in stores:
        try:
            present = run_dir in store.list_run_dirs()
        except Exception as e:  # noqa: BLE001
            results.append({"type": name, "error": str(e)[:200]})
            continue
        if not present:
            results.append({"type": name, "skipped": "not present"})
            continue
        size = 0
        try:
            size = sum(int(f.get("size", 0) or 0) for f in store.list_files()
                       if f["name"].split("/", 1)[0] == run_dir)
        except Exception:  # noqa: BLE001
            pass
        if dry_run:
            results.append({"type": name, "would_delete": run_dir, "bytes": size})
            continue
        store.delete_run_dir(run_dir, log)
        results.append({"type": name, "deleted": run_dir, "bytes": size})
    return {"name": cfg.name, "run_dir": run_dir, "dry_run": dry_run,
            "results": results}


def cmd_verify(cfg: Config, log: logging.Logger) -> int:
    log.info("Config OK, name: %s", cfg.name)

    if not cfg.sources:
        log.error("Model has no 'sources:' — nothing would be backed up.")
        return 1

    # Triggers preflight
    for tcfg in cfg.triggers:
        t = build_trigger(tcfg, log)
        try:
            t.check()
        except Exception as e:
            log.error("Trigger %s: %s", t.type, e)
            return 1

    # Sources preflight (just basic — non-matching is a warning, not error)
    for scfg in cfg.sources:
        s = build_source(scfg, log)
        try:
            s.check_access()
            log.info("Source %s: paths=%s", s.type, s.paths)
        except Exception as e:
            log.error("Source %s: %s", s.type, e)
            return 1

    # Local
    if cfg.local.enabled:
        build_local(cfg, log).check_access()

    # Destinations summary — show every storage section's status so the user
    # can spot "ah, I forgot to enable S3" before running a backup.
    _log_destinations_summary(cfg, log)

    # Destinations
    for d in build_destinations(cfg, log):
        try:
            d.check_access()
        except Exception as e:
            log.error("%s: %s", d.name, e)
            return 1

    # Encryption preflight
    if cfg.encryption.enabled:
        method = cfg.encryption.method.lower()
        if method == "gpg-symmetric":
            if not cfg.encryption.passphrase_file or \
               not Path(cfg.encryption.passphrase_file).exists():
                log.error("encryption: passphrase_file missing")
                return 1
        elif method == "gpg-asymmetric":
            if not cfg.encryption.recipient:
                log.error("encryption: recipient required")
                return 1
        elif method == "openssl":
            if not cfg.encryption.openssl_pass_file or \
               not Path(cfg.encryption.openssl_pass_file).exists():
                log.error("encryption: openssl_pass_file missing")
                return 1

    log.info("Verify: OK")
    return 0


def cmd_models(config_paths: list) -> int:
    """List discovered models in a directory."""
    from pathlib import Path as _P
    print(f"{'Model':<20} {'Triggers':<25} {'Sources':<15} {'Destinations'}")
    print("-" * 90)
    for p in config_paths:
        try:
            cfg = Config.load(str(p))
        except Exception as e:
            print(f"{_P(p).stem:<20} (failed: {e})")
            continue

        trig = ", ".join(t.get("type", "?") for t in cfg.triggers) or "(none)"
        src = ", ".join(s.get("type", "files") for s in cfg.sources) or "(none)"
        dests = []
        if cfg.local.enabled:
            dests.append("local")
        for n in ("webdav", "s3", "sftp", "dropbox", "gcs", "azure"):
            if getattr(getattr(cfg, n), "enabled", False):
                dests.append(n)
        print(f"{cfg.name:<20} {trig:<25} {src:<15} {', '.join(dests)}")
    return 0
