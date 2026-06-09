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
import logging
import logging.handlers
import socket
import subprocess
import sys
import tempfile
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
                log.warning("No artifacts produced — nothing to upload.")
                # fall through — notify will fire as 'warning' due to the
                # collected warning above

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

    except Exception:
        tb = traceback.format_exc()
        log.error("FAILED:\n%s", tb)
        run_hooks("on_failure", cfg.hooks.on_failure, log)
        run_hooks("after", cfg.hooks.after, log)
        log.removeHandler(warn_collector)
        body = f"Backup failed at {started:%Y-%m-%d %H:%M:%S}\n\n{tb}"
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
