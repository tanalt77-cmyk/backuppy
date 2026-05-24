"""Configuration dataclasses and YAML loader."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import yaml


# ============================================================================
# Archive (file backup)
# ============================================================================

@dataclass
class ArchiveCfg:
    name: str = "files"
    paths: list[str] = field(default_factory=list)
    excludes: list[str] = field(default_factory=list)


# ============================================================================
# Compressors
# ============================================================================

@dataclass
class CompressionCfg:
    """Method applied to tar archives (databases produce raw dumps, then this)."""
    method: str = "gzip"  # gzip | bzip2 | xz | zstd | none
    level: int | None = None  # 1-9 for gzip/bzip2/xz, 1-22 for zstd; None = default


# ============================================================================
# Encryption
# ============================================================================

@dataclass
class EncryptionCfg:
    enabled: bool = False
    method: str = "gpg-symmetric"  # gpg-symmetric | gpg-asymmetric | openssl
    # gpg-symmetric:
    passphrase_file: str | None = None
    # gpg-asymmetric:
    recipient: str | None = None       # email or key ID for GPG
    gpg_home: str | None = None        # custom GNUPGHOME if needed
    # openssl AES-256-CBC:
    openssl_pass_file: str | None = None


# ============================================================================
# Databases
# ============================================================================

@dataclass
class MSSQLDatabase:
    name: str
    backup_type: str = "FULL"  # FULL | DIFFERENTIAL


@dataclass
class MSSQLCfg:
    enabled: bool = False
    host: str = ""
    port: int = 1433
    username: str = ""
    password: str = ""
    databases: list[MSSQLDatabase] = field(default_factory=list)
    remote_backup_dir: str = ""
    local_mount_dir: str = ""
    compression: bool = True
    checksum: bool = True
    copy_only: bool = False
    timeout: int = 7200
    cleanup_remote: bool = True
    encrypt_connection: bool = True
    trust_server_certificate: bool = True


@dataclass
class PostgresCfg:
    enabled: bool = False
    host: str = "localhost"
    port: int = 5432
    username: str = "postgres"
    password: str = ""           # or use ~/.pgpass / env PGPASSWORD
    databases: list[str] = field(default_factory=list)  # empty = all DBs via pg_dumpall
    format: str = "custom"       # custom | plain | tar | directory
    extra_args: list[str] = field(default_factory=list)
    pg_dump_path: str = "pg_dump"
    pg_dumpall_path: str = "pg_dumpall"


@dataclass
class MySQLCfg:
    enabled: bool = False
    host: str = "localhost"
    port: int = 3306
    username: str = "root"
    password: str = ""
    databases: list[str] = field(default_factory=list)  # empty = --all-databases
    single_transaction: bool = True  # safe online dump for InnoDB
    routines: bool = True
    triggers: bool = True
    events: bool = True
    extra_args: list[str] = field(default_factory=list)
    mysqldump_path: str = "mysqldump"


@dataclass
class MongoCfg:
    enabled: bool = False
    uri: str = ""                # mongodb://user:pass@host:27017/dbname?authSource=admin
    databases: list[str] = field(default_factory=list)  # empty = all
    gzip: bool = True            # mongodump --gzip
    oplog: bool = False          # mongodump --oplog (point-in-time consistency)
    extra_args: list[str] = field(default_factory=list)
    mongodump_path: str = "mongodump"


@dataclass
class RedisCfg:
    enabled: bool = False
    host: str = "localhost"
    port: int = 6379
    password: str = ""
    rdb_path: str = "/var/lib/redis/dump.rdb"  # where Redis writes RDB
    use_bgsave: bool = True       # trigger BGSAVE before copy
    save_timeout: int = 300       # wait BGSAVE up to N seconds
    redis_cli_path: str = "redis-cli"


@dataclass
class SQLiteCfg:
    enabled: bool = False
    databases: list[str] = field(default_factory=list)  # paths to .db/.sqlite files
    use_online_backup: bool = True  # use sqlite3 .backup (safe while DB is in use)


# ============================================================================
# Storages
# ============================================================================

@dataclass
class LocalCfg:
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
    multipart_chunksize_mb: int = 16


@dataclass
class SFTPCfg:
    enabled: bool = False
    host: str = ""
    port: int = 22
    username: str = ""
    password: str = ""              # or use key_file
    key_file: str = ""              # path to private key
    key_passphrase: str = ""
    remote_path: str = "backups"
    keep_last: int = 10
    known_hosts: str = ""           # path to known_hosts; empty = strict checking off (insecure)


@dataclass
class DropboxCfg:
    enabled: bool = False
    access_token: str = ""          # OAuth2 token from app console
    refresh_token: str = ""         # for long-lived auth (recommended)
    app_key: str = ""
    app_secret: str = ""
    remote_path: str = "/Backups"   # MUST start with /
    keep_last: int = 30
    chunk_size_mb: int = 16


@dataclass
class GCSCfg:
    enabled: bool = False
    bucket: str = ""
    prefix: str = "backups"
    credentials_file: str = ""      # path to service account JSON; empty = GOOGLE_APPLICATION_CREDENTIALS
    project_id: str = ""
    storage_class: str = "STANDARD"  # STANDARD | NEARLINE | COLDLINE | ARCHIVE
    keep_last: int = 30


@dataclass
class AzureBlobCfg:
    enabled: bool = False
    account_name: str = ""
    account_key: str = ""           # or use connection_string
    connection_string: str = ""
    container: str = ""
    prefix: str = "backups"
    keep_last: int = 30
    tier: str = "Hot"                # Hot | Cool | Archive


# ============================================================================
# Notifiers
# ============================================================================

@dataclass
class EmailCfg:
    enabled: bool = False
    when: str = "on_failure"        # always | on_failure
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
    when: str = "on_failure"        # always | on_failure
    bot_token: str = ""             # from @BotFather
    chat_id: str = ""               # personal or group chat ID; can be string with @channel
    timeout: int = 30


# ============================================================================
# Pro features
# ============================================================================

@dataclass
class SplitterCfg:
    enabled: bool = False
    chunk_size_mb: int = 1024       # split files into N-MB chunks
    only_above_mb: int = 0          # split only files larger than this


@dataclass
class ThrottleCfg:
    enabled: bool = False
    upload_rate_kbps: int = 0       # max kilobytes/sec during upload; 0 = unlimited


@dataclass
class VerifyCfg:
    enabled: bool = True            # always good to leave on
    method: str = "size"            # size | checksum
    # 'size' is fast (just compares bytes); 'checksum' computes SHA256 of local
    # and compares with what storage reports (where supported: S3 ETag for non-multipart,
    # GCS md5, Azure md5, etc.). For storages without checksum API — falls back to size.


@dataclass
class HooksCfg:
    """Shell commands to run at certain points. Non-zero exit = treated as warning
    unless command starts with '!' (then it's a hard error that aborts the run)."""
    before: list[str] = field(default_factory=list)
    after: list[str] = field(default_factory=list)
    on_success: list[str] = field(default_factory=list)
    on_failure: list[str] = field(default_factory=list)


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

    archive: ArchiveCfg = field(default_factory=ArchiveCfg)
    compression: CompressionCfg = field(default_factory=CompressionCfg)
    encryption: EncryptionCfg = field(default_factory=EncryptionCfg)

    # Databases
    mssql: MSSQLCfg = field(default_factory=MSSQLCfg)
    postgres: PostgresCfg = field(default_factory=PostgresCfg)
    mysql: MySQLCfg = field(default_factory=MySQLCfg)
    mongodb: MongoCfg = field(default_factory=MongoCfg)
    redis: RedisCfg = field(default_factory=RedisCfg)
    sqlite: SQLiteCfg = field(default_factory=SQLiteCfg)

    # Storages
    local: LocalCfg = field(default_factory=LocalCfg)
    webdav: WebDAVCfg = field(default_factory=WebDAVCfg)
    s3: S3Cfg = field(default_factory=S3Cfg)
    sftp: SFTPCfg = field(default_factory=SFTPCfg)
    dropbox: DropboxCfg = field(default_factory=DropboxCfg)
    gcs: GCSCfg = field(default_factory=GCSCfg)
    azure: AzureBlobCfg = field(default_factory=AzureBlobCfg)

    # Notifiers
    email: EmailCfg = field(default_factory=EmailCfg)
    telegram: TelegramCfg = field(default_factory=TelegramCfg)

    # Pro
    splitter: SplitterCfg = field(default_factory=SplitterCfg)
    throttle: ThrottleCfg = field(default_factory=ThrottleCfg)
    verify: VerifyCfg = field(default_factory=VerifyCfg)
    hooks: HooksCfg = field(default_factory=HooksCfg)

    log: LogCfg = field(default_factory=LogCfg)

    @staticmethod
    def load(path: str) -> "Config":
        """Load a config file, resolving 'extends:' references first.

        extends: can be a string or list of paths. Paths are relative to the
        file that contains the extends directive. The current file's values
        override the inherited ones (deep merge for dicts, replace for lists
        and scalars).
        """
        raw = _load_with_extends(Path(path).resolve(), set())
        return _build_config(raw)

    @staticmethod
    def from_dict(raw: dict[str, Any]) -> "Config":
        """Build a Config from already-merged dict (used by extends + tests)."""
        return _build_config(raw)


def _load_with_extends(path: Path, seen: set[Path]) -> dict[str, Any]:
    """Recursively load YAML, resolving extends. Returns merged dict."""
    if path in seen:
        raise RuntimeError(
            f"extends cycle detected: {path} already in {seen}"
        )
    if not path.exists():
        raise FileNotFoundError(f"Config not found: {path}")

    with open(path, "r", encoding="utf-8") as f:
        current = yaml.safe_load(f) or {}

    extends = current.pop("extends", None)
    if not extends:
        return current

    if isinstance(extends, str):
        extends = [extends]
    if not isinstance(extends, list):
        raise ValueError(f"extends must be string or list, got {type(extends)}")

    # Resolve each parent relative to THIS file's directory, then merge in order.
    # Later children win over earlier; current file wins over all parents.
    base: dict[str, Any] = {}
    seen_now = seen | {path}
    for parent in extends:
        parent_path = (path.parent / parent).resolve()
        parent_data = _load_with_extends(parent_path, seen_now)
        base = _deep_merge(base, parent_data)

    return _deep_merge(base, current)


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Merge overlay into base. Dicts merged recursively; lists/scalars replaced."""
    out = dict(base)
    for key, val in overlay.items():
        if (key in out and isinstance(out[key], dict)
                and isinstance(val, dict)):
            out[key] = _deep_merge(out[key], val)
        else:
            out[key] = val
    return out


def _build_config(raw: dict[str, Any]) -> Config:
    """Construct Config dataclass from a merged dict."""
    mssql_raw = dict(raw.get("mssql", {}))
    mssql_dbs = mssql_raw.pop("databases", [])
    mssql = MSSQLCfg(**mssql_raw)
    mssql.databases = [
        MSSQLDatabase(**d) if isinstance(d, dict) else MSSQLDatabase(name=d)
        for d in mssql_dbs
    ]

    return Config(
        name=raw.get("name", "backup"),
        archive=ArchiveCfg(**raw.get("archive", {})),
        compression=CompressionCfg(**raw.get("compression", {})),
        encryption=EncryptionCfg(**raw.get("encryption", {})),
        mssql=mssql,
        postgres=PostgresCfg(**raw.get("postgres", {})),
        mysql=MySQLCfg(**raw.get("mysql", {})),
        mongodb=MongoCfg(**raw.get("mongodb", {})),
        redis=RedisCfg(**raw.get("redis", {})),
        sqlite=SQLiteCfg(**raw.get("sqlite", {})),
        local=LocalCfg(**raw.get("local", {})),
        webdav=WebDAVCfg(**raw.get("webdav", {})),
        s3=S3Cfg(**raw.get("s3", {})),
        sftp=SFTPCfg(**raw.get("sftp", {})),
        dropbox=DropboxCfg(**raw.get("dropbox", {})),
        gcs=GCSCfg(**raw.get("gcs", {})),
        azure=AzureBlobCfg(**raw.get("azure", {})),
        email=EmailCfg(**raw.get("email", {})),
        telegram=TelegramCfg(**raw.get("telegram", {})),
        splitter=SplitterCfg(**raw.get("splitter", {})),
        throttle=ThrottleCfg(**raw.get("throttle", {})),
        verify=VerifyCfg(**raw.get("verify", {})),
        hooks=HooksCfg(**raw.get("hooks", {})),
        log=LogCfg(**raw.get("log", {})),
    )
