"""Core orchestration: archive building, hooks, run/list/verify commands."""
from __future__ import annotations

import datetime as dt
import fnmatch
import logging
import logging.handlers
import os
import socket
import subprocess
import sys
import tarfile
import tempfile
import traceback
from pathlib import Path
from typing import Iterable

from .config import Config, LogCfg, HooksCfg
from .compress import compress_file, file_extension_for
from .encrypt import encrypt_file
from .splitter import split_file
from .notify import notify_all
from .databases import (
    BaseDumper, MSSQLDumper, PostgresDumper, MySQLDumper,
    MongoDumper, RedisDumper, SQLiteDumper,
)
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
# File archive
# ============================================================================

def _matches_any(path: str, patterns: Iterable[str]) -> bool:
    return any(fnmatch.fnmatch(path, p) for p in patterns)


def make_archive(cfg: Config, work_dir: Path, log: logging.Logger) -> Path:
    """Create a tar (uncompressed; compression handled separately)."""
    timestamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    archive_name = f"{cfg.name}-{cfg.archive.name}-{timestamp}.tar"
    archive_path = work_dir / archive_name
    log.info("Creating archive: %s", archive_path)

    excludes = cfg.archive.excludes
    skipped = 0

    def filt(tarinfo: tarfile.TarInfo) -> tarfile.TarInfo | None:
        nonlocal skipped
        if _matches_any(tarinfo.name, excludes) or _matches_any(
            "/" + tarinfo.name, excludes
        ):
            skipped += 1
            return None
        return tarinfo

    with tarfile.open(archive_path, "w") as tar:
        for src in cfg.archive.paths:
            src_path = Path(src)
            if not src_path.exists():
                log.warning("Skipping non-existent path: %s", src)
                continue
            log.info("  adding %s", src)
            tar.add(src, arcname=src_path.name, filter=filt)

    size_mb = archive_path.stat().st_size / 1024 / 1024
    log.info("Archive ready: %.2f MB (excluded: %d)", size_mb, skipped)
    return archive_path


# ============================================================================
# Hooks
# ============================================================================

def run_hooks(stage: str, commands: list[str], log: logging.Logger) -> None:
    """Run shell hooks. Commands prefixed with '!' are hard errors on failure."""
    if not commands:
        return
    log.info("Hooks: running %d %s command(s)", len(commands), stage)
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
# Dumpers & storages factory
# ============================================================================

def build_dumpers(cfg: Config, log: logging.Logger) -> list[BaseDumper]:
    out: list[BaseDumper] = []
    if cfg.mssql.enabled and cfg.mssql.databases:
        out.append(MSSQLDumper(cfg.mssql, log))
    if cfg.postgres.enabled:
        out.append(PostgresDumper(cfg.postgres, log))
    if cfg.mysql.enabled:
        out.append(MySQLDumper(cfg.mysql, log))
    if cfg.mongodb.enabled:
        out.append(MongoDumper(cfg.mongodb, log))
    if cfg.redis.enabled:
        out.append(RedisDumper(cfg.redis, log))
    if cfg.sqlite.enabled and cfg.sqlite.databases:
        out.append(SQLiteDumper(cfg.sqlite, log))
    return out


def build_remote_storages(cfg: Config, log: logging.Logger) -> list[BaseStorage]:
    out: list[BaseStorage] = []
    if cfg.webdav.enabled:
        out.append(WebDAVStorage(cfg.webdav, log))
    if cfg.s3.enabled:
        out.append(S3Storage(cfg.s3, log))
    if cfg.sftp.enabled:
        out.append(SFTPStorage(cfg.sftp, log))
    if cfg.dropbox.enabled:
        out.append(DropboxStorage(cfg.dropbox, log))
    if cfg.gcs.enabled:
        out.append(GCSStorage(cfg.gcs, log))
    if cfg.azure.enabled:
        out.append(AzureBlobStorage(cfg.azure, log))
    return out


def keep_last_for(storage: BaseStorage, cfg: Config) -> int:
    """Each storage has its own keep_last in its config section."""
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
    host = socket.gethostname()
    started = dt.datetime.now()
    log.info("=== backuppy '%s' start on %s ===", cfg.name, host)

    dumpers = build_dumpers(cfg, log)
    remote_storages = build_remote_storages(cfg, log)
    local_storage = LocalStorage(cfg.local, log)

    if dry_run:
        log.info("[DRY-RUN] no changes will be made")
        for d in dumpers:
            log.info("  dumper: %s, prefixes=%s",
                     type(d).__name__, d.prefixes())
        if cfg.archive.paths:
            log.info("  archive: %s (excludes: %s)",
                     cfg.archive.paths, cfg.archive.excludes)
        log.info("  compression: %s", cfg.compression.method)
        if cfg.encryption.enabled:
            log.info("  encryption: %s", cfg.encryption.method)
        log.info("  local: %s (keep=%d)", cfg.local.path, cfg.local.keep_last)
        for s in remote_storages:
            log.info("  remote: %s (keep=%d)", s.name, keep_last_for(s, cfg))
        return 0

    try:
        run_hooks("before", cfg.hooks.before, log)
    except RuntimeError as e:
        log.error("Aborting: %s", e)
        return 1

    success = False
    try:
        with tempfile.TemporaryDirectory(prefix="backuppy-") as tmp:
            work = Path(tmp)
            artifacts: list[Path] = []

            # 1. Databases
            for d in dumpers:
                artifacts.extend(d.dump_all(work))

            # 2. File archive
            if cfg.archive.paths:
                artifacts.append(make_archive(cfg, work, log))

            if not artifacts:
                log.warning("Nothing to back up")
                run_hooks("after", cfg.hooks.after, log)
                return 0

            # 3. Process each artifact independently
            for art in artifacts:
                art = compress_file(art, cfg.compression, log)
                art = encrypt_file(art, cfg.encryption, log)

                # Splitter: produces multiple files for very large ones
                parts = split_file(art, cfg.splitter, log)

                # Store each part: local first, then remote uploads
                for part in parts:
                    local_dest = local_storage.store(part)
                    for storage in remote_storages:
                        _upload_with_verify(storage, local_dest, cfg, log)

            # 4. Rotation
            _rotate_all(cfg, dumpers, local_storage, remote_storages, log)

        success = True
    except Exception:
        tb = traceback.format_exc()
        log.error("FAILED:\n%s", tb)
        run_hooks("on_failure", cfg.hooks.on_failure, log)
        run_hooks("after", cfg.hooks.after, log)
        notify_all(cfg.email, cfg.telegram, "on_failure",
                   f"[backuppy] FAIL {cfg.name} @ {host}",
                   f"Backup failed at {started:%Y-%m-%d %H:%M:%S}\n\n{tb}",
                   log)
        return 1

    elapsed = (dt.datetime.now() - started).total_seconds()
    log.info("=== Done in %.1fs ===", elapsed)

    run_hooks("on_success", cfg.hooks.on_success, log)
    run_hooks("after", cfg.hooks.after, log)
    notify_all(cfg.email, cfg.telegram, "always",
               f"[backuppy] OK {cfg.name} @ {host}",
               f"Backup finished successfully in {elapsed:.1f}s.",
               log)
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


def _rotate_all(cfg: Config, dumpers: list[BaseDumper],
                local: LocalStorage, remotes: list[BaseStorage],
                log: logging.Logger) -> None:
    prefixes: list[str] = []
    if cfg.archive.paths:
        prefixes.append(f"{cfg.name}-{cfg.archive.name}-")
    for d in dumpers:
        prefixes.extend(d.prefixes())

    # Local — also rotates by prefix now
    for p in prefixes:
        local.rotate(p, cfg.local.keep_last, log)

    for s in remotes:
        keep = keep_last_for(s, cfg)
        for p in prefixes:
            s.rotate(p, keep, log)


def cmd_list(cfg: Config, log: logging.Logger) -> int:
    log.info("Local backups in %s:", cfg.local.path)
    local = LocalStorage(cfg.local, log)
    for f in sorted(local.list_files(), key=lambda x: x["name"]):
        mb = f["size"] / 1024 / 1024
        print(f"  {f['name']}  ({mb:.2f} MB)")

    for s in build_remote_storages(cfg, log):
        log.info("Backups in %s:", s.name)
        try:
            for f in sorted(s.list_files(), key=lambda x: x["name"]):
                mb = f["size"] / 1024 / 1024
                modified = f.get("modified", "")
                print(f"  {f['name']}  ({mb:.2f} MB, {modified})")
        except Exception as e:
            log.error("  %s: %s", s.name, e)
    return 0


def cmd_verify(cfg: Config, log: logging.Logger) -> int:
    log.info("Config OK, name: %s", cfg.name)
    for src in cfg.archive.paths:
        if not Path(src).exists():
            log.warning("  archive path missing: %s", src)
        else:
            log.info("  archive path: %s", src)

    # Local
    LocalStorage(cfg.local, log).check_access()

    # Dumpers
    for d in build_dumpers(cfg, log):
        try:
            d.check_connection()
        except Exception as e:
            log.error("%s: %s", type(d).__name__, e)
            return 1

    # Remote storages
    for s in build_remote_storages(cfg, log):
        try:
            s.check_access()
        except Exception as e:
            log.error("%s: %s", s.name, e)
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
    """List configured models discovered from a list of config paths."""
    from pathlib import Path as _P
    print(f"{'Model':<20} {'Sources':<45} {'Destinations'}")
    print("-" * 90)
    for p in config_paths:
        try:
            cfg = Config.load(str(p))
        except Exception as e:
            print(f"{_P(p).stem:<20} (failed to load: {e})")
            continue

        sources: list[str] = []
        if cfg.archive.paths:
            sources.append(f"files({len(cfg.archive.paths)})")
        if cfg.mssql.enabled and cfg.mssql.databases:
            full = sum(1 for d in cfg.mssql.databases
                       if d.backup_type.upper() == "FULL")
            diff = sum(1 for d in cfg.mssql.databases
                       if d.backup_type.upper() == "DIFFERENTIAL")
            label = f"mssql({len(cfg.mssql.databases)})"
            if diff and not full:
                label += "[DIFF]"
            elif diff and full:
                label += "[mix]"
            sources.append(label)
        if cfg.postgres.enabled:
            sources.append(
                f"postgres({len(cfg.postgres.databases) or 'all'})"
            )
        if cfg.mysql.enabled:
            sources.append(
                f"mysql({len(cfg.mysql.databases) or 'all'})"
            )
        if cfg.mongodb.enabled:
            sources.append(
                f"mongo({len(cfg.mongodb.databases) or 'all'})"
            )
        if cfg.redis.enabled:
            sources.append("redis")
        if cfg.sqlite.enabled and cfg.sqlite.databases:
            sources.append(f"sqlite({len(cfg.sqlite.databases)})")

        dests: list[str] = ["local"]
        for name in ("webdav", "s3", "sftp", "dropbox", "gcs", "azure"):
            if getattr(getattr(cfg, name), "enabled", False):
                dests.append(name)

        sources_str = ", ".join(sources) if sources else "(none)"
        if len(sources_str) > 43:
            sources_str = sources_str[:40] + "..."
        print(f"{cfg.name:<20} {sources_str:<45} {', '.join(dests)}")
    return 0
