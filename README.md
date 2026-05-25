# backuppy

A modular backup tool for Linux servers, inspired by the (no longer maintained)
[Ruby Backup gem](https://github.com/backup/backup) but built for 2026.
Written in Python, single YAML config, no daemons, no agents, no databases of
its own.

## Architecture

Every backup model is a simple pipeline:

```
TRIGGERS  →  SOURCES  →  PROCESSING  →  DESTINATIONS
(optional)   (required)   (compression,   (local, webdav, s3, ...)
                          encryption,
                          splitter, ...)
```

- **Triggers** *produce* files (run `BACKUP DATABASE`, `pg_dump`, `rsync`, any shell command).
- **Sources** *pick up* files from one or more paths or glob patterns. They don't care
  where the files came from — a local folder, an SMB mount, an NFS mount — all equal.
- **Destinations** *upload* the processed files to local disk, Nextcloud (WebDAV),
  S3, SFTP, Dropbox, GCS, or Azure Blob.

Triggers and sources are independent. A trigger doesn't know where its output will go.
A source doesn't know who put the files in its path. This makes everything composable.

## Quick install

```bash
# Install curl first if needed (on minimal Debian/Ubuntu)
apt install -y curl

# One-line installer
curl -fsSL https://raw.githubusercontent.com/tanalt77-cmyk/backuppy/main/install.sh | bash
```

Options:

```bash
bash install.sh --no-mssql              # skip MSSQL ODBC driver
bash install.sh --extras s3,sftp        # only specific backends
bash install.sh -s -- --update          # update existing install
```

## Usage

```bash
# By model name (looks in /etc/backuppy/):
backuppy run pixo                          # run /etc/backuppy/pixo.yml
backuppy verify pixo                       # preflight check
backuppy run pixo files-www mssql-prod     # run multiple models
backuppy run --all                         # run everything in /etc/backuppy/
backuppy models                            # list discovered models
backuppy list pixo                         # show stored backups for a model

# By explicit path (works anywhere):
backuppy run -c /path/to/some.yml --dry-run
```

## Creating models

The fastest way to start: generate a model from a template.

```bash
backuppy new --list                       # see available templates
backuppy new pixo --template mssql-full   # create /etc/backuppy/pixo.yml
backuppy new app-files --template files
backuppy new pg-prod --template postgres
```

Generated files include comments explaining every field. Open and edit:

```bash
nano /etc/backuppy/pixo.yml      # fill in TODO placeholders
chmod 600 /etc/backuppy/pixo.yml # protect credentials
```

Available templates:

| Template          | What it generates                                       |
|-------------------|---------------------------------------------------------|
| `files`           | Filesystem archive (paths + excludes)                   |
| `postgres`        | PostgreSQL via pg_dump                                  |
| `mysql`           | MySQL/MariaDB via mysqldump                             |
| `mssql-full`      | Windows SQL Server FULL backup                          |
| `mssql-diff`      | Windows SQL Server DIFFERENTIAL backup                  |
| `mssql-log`       | Windows SQL Server TRANSACTION LOG backup               |
| `shared-storage`  | Shared destination credentials (for use with `extends:`)|

The template is auto-detected from the name if `--template` is omitted:
`backuppy new mssql-prod` → uses `mssql-full` template.

Generated files default to `/etc/backuppy/`. Override with `--dir`:

```bash
backuppy new pixo --template mssql-full --dir /tmp/configs/
```

## Examples

### Just files (no trigger)

Files already exist on disk — just pack, compress, upload.

```yaml
name: web-content
sources:
  - type: files
    paths: [/etc, /var/www]
    excludes: ["*/node_modules/*"]
    archive_name: web                # pack into one tar
compression:
  method: zstd
  level: 6
local:
  enabled: true
  path: /var/backups/backuppy/web-content
  keep_last: 7
webdav:
  enabled: true
  base_url: "https://nextcloud.example.com/remote.php/dav/files/USER/"
  remote_path: "Backups/web-content"
  username: USER
  password: APP-PASSWORD
  keep_last: 30
```

### MSSQL on Windows → Nextcloud

The trigger runs `BACKUP DATABASE` over TCP/1433. SQL Server writes the .bak
to `D:\Backups\backuppy` on the Windows side. You arrange (via SMB mount, etc)
for that path to appear as `/mnt/win-server1/backuppy` on Linux. backuppy
doesn't care HOW — it just looks at the Linux path.

```yaml
name: mssql-prod

triggers:
  - type: mssql
    host: "10.0.0.5"
    username: "backup_user"
    password: "SECRET"
    output_dir_windows: "D:\\Backups\\backuppy"
    databases:
      - { name: AppDB, backup_type: FULL }

sources:
  - type: files
    paths: ["/mnt/win-server1/backuppy/*.bak"]
    delete_after_pickup: false       # keep .bak on Windows too

compression:
  method: zstd
  level: 10

local:
  enabled: true
  path: /var/backups/backuppy/mssql-prod
  keep_last: 7

webdav:
  enabled: true
  base_url: "https://nextcloud.example.com/remote.php/dav/files/USER/"
  remote_path: "Backups/mssql-prod"
  username: USER
  password: APP-PASSWORD
  keep_last: 30
```

### MSSQL with single local copy (static names + timestamped upload)

By default, every MSSQL backup creates a unique file:
`work-full-20260525-021713.bak`. These accumulate on the Windows side and
need cleanup (`delete_after_pickup: true` or manual rotation on Windows).

Alternative: tell SQL Server to write to a *static* filename like
`work-full.bak` — each new backup overwrites the previous local copy.
backuppy then renames with a timestamp when uploading, so cloud history
is still complete.

```yaml
name: pixo

triggers:
  - type: mssql
    host: "10.0.0.5"
    username: backup_user
    password: SECRET
    output_dir_windows: "D:\\Backups\\backuppy"
    databases:
      - { name: work, backup_type: FULL }
      - { name: avic, backup_type: FULL }
    compression: true
    static_local_name: true              # SQL writes work-full.bak (no timestamp)

sources:
  - type: files
    paths: ["/mnt/win-server1/backuppy/*.bak"]
    rename_with_timestamp: true          # adds timestamp to uploaded names
    delete_after_pickup: false           # not needed — files overwrite themselves

local:
  enabled: false                         # SQL Server folder IS the local copy

webdav:
  enabled: true
  base_url: "https://nextcloud.example.com/remote.php/dav/files/USER/"
  remote_path: "Backups/pixo/full"
  username: USER
  password: APP-PASSWORD
  keep_last: 7                           # rotated normally by prefix
```

Result:
- **On Windows**: only `work-full.bak`, `avic-full.bak` — one fresh copy per DB.
- **In Nextcloud**: `work-full-20260525-021713.bak` history, last 7 kept.

Pair with `mssql-diff` (DIFFERENTIAL) and `mssql-log` (transaction log)
models for full point-in-time recovery.

### Custom hook trigger

Any shell command can be a trigger. Useful for rsync, custom scripts, anything.

```yaml
triggers:
  - type: hook
    command: "rsync -av webserver:/var/www /tmp/staging/"
    timeout: 1800
sources:
  - type: files
    paths: ["/tmp/staging/*"]
    delete_after_pickup: true
```

## Multiple models

backuppy treats each YAML file as a model. Run individually, in groups, or all:

```bash
backuppy run pixo                          # one model by name
backuppy run pixo files-www                # multiple models by name
backuppy run --all                         # all models in /etc/backuppy/
backuppy run -c /path/to/external.yml      # external file by path
```

Share credentials between models with `extends:`:

```yaml
# /etc/backuppy/shared/storage.yml — credentials in one place
webdav:
  enabled: true
  base_url: "https://..."
  username: USER
  password: APP-PASSWORD

# /etc/backuppy/my-model.yml — only what's unique
extends:
  - shared/storage.yml
name: my-model
sources:
  - type: files
    paths: [/data]
webdav:
  remote_path: "Backups/my-model"
  keep_last: 30
```

## Triggers

| Type       | What it does                                              |
|------------|-----------------------------------------------------------|
| `mssql`    | Connects to MSSQL over TCP/1433, runs `BACKUP DATABASE`   |
| `postgres` | Runs `pg_dump` or `pg_dumpall` to an output directory     |
| `mysql`    | Runs `mysqldump` to an output directory                   |
| `mongodb`  | Runs `mongodump`, packs to tar                            |
| `redis`    | Runs `BGSAVE`, copies the RDB file                        |
| `sqlite`   | Online `.backup` API (safe on live DBs)                   |
| `hook`     | Runs any shell command                                    |

## Sources

| Type      | What it does                                              |
|-----------|-----------------------------------------------------------|
| `files`   | Picks up files matching paths/glob patterns               |

The `files` source is universal: it handles local folders, SMB mounts, NFS
mounts, sshfs, anything reachable from this Linux box. The optional
`archive_name` makes it pack everything into a single tar; otherwise each
file is uploaded individually.

## Destinations

| Backend  | Backend-specific options                                     |
|----------|--------------------------------------------------------------|
| `local`  | path, keep_last                                              |
| `webdav` | Nextcloud, Hetzner Storage Share, ownCloud                   |
| `s3`     | Amazon S3 + compatible (Wasabi, Backblaze B2, MinIO)         |
| `sftp`   | SSH key or password                                          |
| `dropbox`| OAuth refresh tokens supported                               |
| `gcs`    | Google Cloud Storage with service account JSON               |
| `azure`  | Azure Blob (Hot/Cool/Archive tiers)                          |

## Compression and encryption

```yaml
compression:
  method: zstd                      # gzip | bzip2 | xz | zstd | none
  level: 10

encryption:
  enabled: true
  method: gpg-symmetric             # gpg-symmetric | gpg-asymmetric | openssl
  passphrase_file: /etc/backuppy/passphrase
```

## Notifications

```yaml
telegram:
  enabled: true
  when: on_failure                  # always | on_failure
  bot_token: "..."
  chat_id: "..."

email:
  enabled: true
  when: on_failure
  smtp_host: smtp.example.com
  ...
```

## Cron

```cron
PATH=/usr/local/bin:/usr/bin:/bin

# Daily 02:00: FULL MSSQL backup
0 2 * * *   /usr/local/bin/backuppy run mssql-full

# Hourly DIFFERENTIAL (except 02:00)
0 0-1,3-23 * * *   /usr/local/bin/backuppy run mssql-diff

# Run everything daily at 03:00
0 3 * * *   /usr/local/bin/backuppy run --all
```

## Restore

backuppy doesn't include a `restore` command — every engine has its own preferred path:

- **Files** — `tar -xzf my-files-*.tar.gz -C /`
- **MSSQL** — `RESTORE DATABASE [AppDB] FROM DISK = N'path\AppDB-full-*.bak' WITH REPLACE`
- **PostgreSQL** (custom) — `pg_restore -d mydb my-pg-*.dump`
- **MySQL** — `mysql mydb < my-mysql-*.sql`

If encrypted, decrypt first; if split, `cat parts > original` first.

## License

MIT — see [LICENSE](LICENSE).
