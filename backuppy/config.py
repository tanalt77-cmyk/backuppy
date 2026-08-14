"""Configuration dataclasses and YAML loader (v3.0 — universal architecture).

Concepts:
- Triggers: optional list of actions that PRODUCE files (run BACKUP DATABASE,
  pg_dump, rsync, shell command, ...). Each trigger writes files somewhere.
- Sources: required list of "where to pick up files" — local paths, glob patterns,
  mounted SMB shares — backuppy doesn't care what they are.
- Destinations: where to send the processed files (local, webdav, s3, ...).
- Processing: compression/encryption/splitter/throttle applied between source and dest.

A model needs at minimum: name + sources + at least one destination.
Triggers are optional (e.g. if files are already on disk you just want uploaded).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import yaml


# ============================================================================
# Triggers — things that produce files
# ============================================================================
# Each trigger is a dict with a 'type' field. Concrete params depend on type.
# We keep them as raw dicts; the trigger factory in core.py builds instances.

# Validated trigger types (for nice errors):
TRIGGER_TYPES = {
    "mssql", "postgres", "mysql", "mongodb", "redis", "sqlite", "hook",
}


# ============================================================================
# Sources — where to pick up files
# ============================================================================

@dataclass
class FilesSource:
    """Pick up files matching paths/glob patterns."""
    type: str = "files"
    paths: list[str] = field(default_factory=list)         # may include glob patterns
    excludes: list[str] = field(default_factory=list)      # exclude patterns
    delete_after_pickup: bool = False                      # delete originals after copy
    archive_name: str = ""                                 # if set, all matched files
                                                            # are tar'd as ONE archive
                                                            # with this name


SOURCE_TYPES = {"files"}


# ============================================================================
# Processing
# ============================================================================

@dataclass
class CompressionCfg:
    method: str = "gzip"           # gzip | bzip2 | xz | zstd | none
    level: int | None = None


@dataclass
class EncryptionCfg:
    enabled: bool = False
    method: str = "gpg-symmetric"  # gpg-symmetric | gpg-asymmetric | openssl
    passphrase_file: str = ""
    recipient: str = ""
    gpg_home: str = ""
    openssl_pass_file: str = ""


@dataclass
class SplitterCfg:
    enabled: bool = False
    chunk_size_mb: int = 1024
    only_above_mb: int = 0


@dataclass
class ThrottleCfg:
    enabled: bool = False
    upload_rate_kbps: int = 0


@dataclass
class VerifyCfg:
    enabled: bool = True
    method: str = "size"           # size | checksum


@dataclass
class HooksCfg:
    before: list[str] = field(default_factory=list)
    after: list[str] = field(default_factory=list)
    on_success: list[str] = field(default_factory=list)
    on_failure: list[str] = field(default_factory=list)


# ============================================================================
# Destinations (storages)
# ============================================================================

@dataclass
class LocalCfg:
    enabled: bool = True           # local is enabled by default
    path: str = "/var/backups/backuppy"
    keep_last: int = 5


@dataclass
class WebDAVCfg:
    enabled: bool = False
    base_url: str = ""
    remote_path: str = "Backups"
    username: str = ""
    password: str = ""
    keep_last: int = 10
    timeout: int = 300
    verify_tls: bool = True
    # Nextcloud chunked upload for large files.
    # If file size >= chunked_threshold_mb, use multi-part upload protocol.
    # Set chunked: false to disable entirely (works only with raw WebDAV servers).
    chunked: bool = True
    chunked_threshold_mb: int = 500     # use chunked for files >= this size
    chunked_chunk_size_mb: int = 50     # size of each chunk
    chunked_retries: int = 3            # retry per-chunk on network errors
    # Server-side assembly (final MOVE) of a large chunked upload can take much
    # longer than `timeout`. 0 = auto: scale the MOVE read-timeout by file size.
    # Set a positive number of seconds to override.
    chunked_assemble_timeout: int = 0
    # Concurrent chunk uploads (1 = sequential). Higher opens several connections
    # at once, which helps when the server throttles per-connection. Peak memory
    # is roughly chunked_chunk_size_mb * chunked_parallel.
    chunked_parallel: int = 1


@dataclass
class S3Cfg:
    enabled: bool = False
    bucket: str = ""
    region: str = "us-east-1"
    prefix: str = "backups"
    access_key_id: str = ""
    secret_access_key: str = ""
    endpoint_url: str | None = None
    storage_class: str = "STANDARD"
    server_side_encryption: str | None = None
    keep_last: int = 30
    multipart_threshold_mb: int = 64
    multipart_chunksize_mb: int = 64
    max_concurrency: int = 8       # parallel multipart parts (1 stream ≈ 10 MB/s)
    max_retries: int = 15           # per-request attempts (adaptive backoff)


@dataclass
class SFTPCfg:
    enabled: bool = False
    host: str = ""
    port: int = 22
    username: str = ""
    password: str = ""
    key_file: str = ""
    key_passphrase: str = ""
    remote_path: str = "backups"
    keep_last: int = 10
    known_hosts: str = ""


@dataclass
class DropboxCfg:
    enabled: bool = False
    access_token: str = ""
    refresh_token: str = ""
    app_key: str = ""
    app_secret: str = ""
    remote_path: str = "/Backups"
    keep_last: int = 30
    chunk_size_mb: int = 16


@dataclass
class GCSCfg:
    enabled: bool = False
    bucket: str = ""
    prefix: str = "backups"
    credentials_file: str = ""
    project_id: str = ""
    storage_class: str = "STANDARD"
    keep_last: int = 30


@dataclass
class AzureBlobCfg:
    enabled: bool = False
    account_name: str = ""
    account_key: str = ""
    connection_string: str = ""
    container: str = ""
    prefix: str = "backups"
    keep_last: int = 30
    tier: str = "Hot"


# ============================================================================
# Notifiers
# ============================================================================

@dataclass
class EmailCfg:
    enabled: bool = False
    when: str = "on_failure"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    use_tls: bool = True
    from_addr: str = ""
    to_addrs: list[str] = field(default_factory=list)


@dataclass
class TelegramCfg:
    enabled: bool = False
    when: str = "on_failure"
    bot_token: str = ""
    chat_id: str = ""
    timeout: int = 30


# ============================================================================
# Logging
# ============================================================================

@dataclass
class LogCfg:
    file: str = "/var/log/backuppy.log"
    max_bytes: int = 5 * 1024 * 1024
    backup_count: int = 5
    level: str = "INFO"


# ============================================================================
# Root config
# ============================================================================

@dataclass
class Config:
    name: str = "backup"

    # When True, every backup run creates a subdirectory named
    # YYYYMMDD-HHMMSS inside each destination (local + remote) and stores
    # all artifacts of that run inside it. Rotation is then per-directory:
    # keep_last keeps that many run-folders.
    # When False (default), files go directly into remote_path with their
    # individual names, and rotation is per-file by filename prefix.
    group_by_run: bool = False

    # Where to put the temporary working directory during a run.
    # Empty string = use Python's tempfile default (typically /tmp).
    # Set to a path on a large disk if backups exceed available space
    # in /tmp (especially if /tmp is on tmpfs / in RAM).
    tmp_dir: str = ""

    # Triggers and sources are lists of raw dicts.
    # core.py iterates them and instantiates the right class for each.
    triggers: list[dict] = field(default_factory=list)
    sources: list[dict] = field(default_factory=list)

    # Processing
    compression: CompressionCfg = field(default_factory=CompressionCfg)
    encryption: EncryptionCfg = field(default_factory=EncryptionCfg)
    splitter: SplitterCfg = field(default_factory=SplitterCfg)
    throttle: ThrottleCfg = field(default_factory=ThrottleCfg)
    verify: VerifyCfg = field(default_factory=VerifyCfg)
    hooks: HooksCfg = field(default_factory=HooksCfg)

    # Destinations
    local: LocalCfg = field(default_factory=LocalCfg)
    # Additional local destinations. `local` above stays the PRIMARY one (kept
    # for backward compatibility and for code/logs that reference cfg.local);
    # `locals` is the full list the engine actually iterates over (store,
    # rotate, verify, prune). Populated by _build_config from either the old
    # `local: {..}` dict or the new `local: [{..}, {..}]` list form.
    locals: list[LocalCfg] = field(default_factory=list)
    webdav: WebDAVCfg = field(default_factory=WebDAVCfg)
    s3: S3Cfg = field(default_factory=S3Cfg)
    sftp: SFTPCfg = field(default_factory=SFTPCfg)
    dropbox: DropboxCfg = field(default_factory=DropboxCfg)
    gcs: GCSCfg = field(default_factory=GCSCfg)
    azure: AzureBlobCfg = field(default_factory=AzureBlobCfg)

    # Notifiers
    email: EmailCfg = field(default_factory=EmailCfg)
    telegram: TelegramCfg = field(default_factory=TelegramCfg)

    log: LogCfg = field(default_factory=LogCfg)

    @staticmethod
    def load(path: str) -> "Config":
        """Load with extends-merging."""
        raw = _load_with_extends(Path(path).resolve(), set())
        return _build_config(raw)

    @staticmethod
    def from_dict(raw: dict[str, Any]) -> "Config":
        return _build_config(raw)


def _load_with_extends(path: Path, seen: set[Path]) -> dict[str, Any]:
    if path in seen:
        raise RuntimeError(f"extends cycle: {path} already in {seen}")
    if not path.exists():
        raise FileNotFoundError(f"Config not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        current = yaml.safe_load(f) or {}

    extends = current.pop("extends", None)
    if not extends:
        return current
    if isinstance(extends, str):
        extends = [extends]

    base: dict[str, Any] = {}
    for parent in extends:
        parent_path = (path.parent / parent).resolve()
        parent_data = _load_with_extends(parent_path, seen | {path})
        base = _deep_merge(base, parent_data)
    return _deep_merge(base, current)


def _deep_merge(base: dict, overlay: dict) -> dict:
    """Deep-merge dicts; lists and scalars are replaced (not concatenated).

    Exception: top-level 'triggers' and 'sources' lists are replaced
    by the child (whole list overrides whole list).
    """
    out = dict(base)
    for key, val in overlay.items():
        if key in out and isinstance(out[key], dict) and isinstance(val, dict):
            out[key] = _deep_merge(out[key], val)
        else:
            out[key] = val
    return out


def _all_locals(node: Any) -> list[LocalCfg]:
    """Parse the `local` config node into a list of LocalCfg.

    Accepts both the historical single-mapping form and a new list form, so
    every existing model keeps working unchanged:
        local: {path: /x, keep_last: 5}          → [one]
        local: [{path: /x}, {path: /y}]          → [two]
        (absent)                                 → [default, enabled]
    """
    if node is None:
        return [LocalCfg()]
    if isinstance(node, dict):
        return [LocalCfg(**node)]
    if isinstance(node, list):
        out = [LocalCfg(**item) for item in node if isinstance(item, dict)]
        return out or [LocalCfg(enabled=False)]
    return [LocalCfg(enabled=False)]


def _primary_local(node: Any) -> LocalCfg:
    """The first local destination — kept as cfg.local for backward
    compatibility (logs, verify, and any code that reads cfg.local.path)."""
    locs = _all_locals(node)
    return locs[0] if locs else LocalCfg(enabled=False)


def _build_config(raw: dict[str, Any]) -> Config:
    # Validate triggers and sources
    triggers = raw.get("triggers", []) or []
    if not isinstance(triggers, list):
        raise ValueError("'triggers' must be a list")
    for i, t in enumerate(triggers):
        if not isinstance(t, dict):
            raise ValueError(f"triggers[{i}] must be a mapping (dict)")
        ttype = t.get("type")
        if not ttype:
            raise ValueError(f"triggers[{i}] missing 'type'")
        if ttype not in TRIGGER_TYPES:
            raise ValueError(
                f"triggers[{i}] unknown type: {ttype!r}. "
                f"Valid: {sorted(TRIGGER_TYPES)}"
            )

    sources = raw.get("sources", []) or []
    if not isinstance(sources, list):
        raise ValueError("'sources' must be a list")
    for i, s in enumerate(sources):
        if not isinstance(s, dict):
            raise ValueError(f"sources[{i}] must be a mapping (dict)")
        stype = s.get("type", "files")
        if stype not in SOURCE_TYPES:
            raise ValueError(
                f"sources[{i}] unknown type: {stype!r}. "
                f"Valid: {sorted(SOURCE_TYPES)}"
            )

    return Config(
        name=raw.get("name", "backup"),
        group_by_run=bool(raw.get("group_by_run", False)),
        tmp_dir=raw.get("tmp_dir", "") or "",
        triggers=triggers,
        sources=sources,
        compression=CompressionCfg(**raw.get("compression", {})),
        encryption=EncryptionCfg(**raw.get("encryption", {})),
        splitter=SplitterCfg(**raw.get("splitter", {})),
        throttle=ThrottleCfg(**raw.get("throttle", {})),
        verify=VerifyCfg(**raw.get("verify", {})),
        hooks=HooksCfg(**raw.get("hooks", {})),
        local=_primary_local(raw.get("local")),
        locals=_all_locals(raw.get("local")),
        webdav=WebDAVCfg(**raw.get("webdav", {})),
        s3=S3Cfg(**raw.get("s3", {})),
        sftp=SFTPCfg(**raw.get("sftp", {})),
        dropbox=DropboxCfg(**raw.get("dropbox", {})),
        gcs=GCSCfg(**raw.get("gcs", {})),
        azure=AzureBlobCfg(**raw.get("azure", {})),
        email=EmailCfg(**raw.get("email", {})),
        telegram=TelegramCfg(**raw.get("telegram", {})),
        log=LogCfg(**raw.get("log", {})),
    )
