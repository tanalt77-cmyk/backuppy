# backuppy

A modular backup tool for Linux servers, inspired by the (no longer maintained)
[Ruby Backup gem](https://github.com/backup/backup). Written in Python, single
YAML config, no daemons, no agents, no databases of its own.

## Why

The Ruby Backup gem stopped being maintained in 2019, and most modern
alternatives are either heavyweight (Bacula, Bareos), tied to one vendor
(Veeam, AWS Backup), or only do part of the job. backuppy fills the gap of
"one YAML file describes everything I want backed up, where it goes, and how
old copies expire" — including the awkward case of backing up **Windows SQL
Server** databases from a Linux host via SMB.

## Features

**Sources (what to back up)**

- Filesystem tarball with include/exclude patterns
- **MSSQL** on remote Windows servers (TCP/1433 + SMB share for the .bak file), FULL or DIFFERENTIAL
- **PostgreSQL** (per-DB via pg_dump, or the whole cluster via pg_dumpall)
- **MySQL / MariaDB** (per-DB or `--all-databases`, with `--single-transaction` for safe online dumps)
- **MongoDB** (mongodump, optional `--oplog` for replica-set consistency)
- **Redis** (BGSAVE + RDB copy)
- **SQLite** (online `.backup` API — safe even on live DBs)

**Processing**

- Compression: gzip, bzip2, xz (multi-core), zstd
- Encryption: GPG symmetric, GPG asymmetric (public-key — encrypt-only on backup host), OpenSSL AES-256-CBC + PBKDF2

**Destinations (where to send the result)**

- Local filesystem
- WebDAV (Nextcloud, ownCloud, Hetzner Storage Share)
- Amazon S3 and S3-compatible (MinIO, Wasabi, Backblaze B2 via S3 API, etc.)
- SFTP (password or SSH key)
- Dropbox (OAuth refresh tokens supported)
- Google Cloud Storage
- Azure Blob Storage

**Pro features**

- **Splitter** — break giant archives into N-MB chunks before upload (for storage backends that limit single-file size)
- **Throttle** — cap upload bandwidth (kbps) to avoid saturating the link during business hours
- **Verify after upload** — size or checksum (MD5 where supported: S3 non-multipart, GCS, Azure)
- **Hooks** — run shell commands `before`, `after`, `on_success`, `on_failure`. Prefix with `!` to make failures abort the run.

**Notifications**

- Email (SMTP + STARTTLS)
- Telegram (bot token + chat ID)

**Other**

- Single YAML config
- Rotation per backup type, per storage (e.g. keep 30 days on S3, 5 on local)
- Three commands: `verify` (preflight), `run` (do it), `list` (show what's stored)
- Dry-run mode

## Quick install

```bash
curl -fsSL https://raw.githubusercontent.com/tanalt77-cmyk/backuppy/main/install.sh | bash
```

The installer:

1. apt-installs Python, git, gpg, cifs-utils, unixodbc, Microsoft ODBC Driver 18
2. Creates a Python venv at `/opt/backuppy/venv`
3. pip-installs the `backuppy` package + all storage extras from this repo
4. Creates `/usr/local/bin/backuppy` wrapper
5. Drops a starter `/etc/backuppy/config.yml`

Options:

```bash
# Slim install — skip MSSQL ODBC driver and only the backends you need:
bash install.sh --no-mssql --extras s3,sftp

# Update later:
curl -fsSL https://raw.githubusercontent.com/tanalt77-cmyk/backuppy/main/install.sh | bash -s -- --update
```

## Usage

```bash
backuppy verify -c /etc/backuppy/config.yml     # preflight: check creds & access
backuppy run    -c /etc/backuppy/config.yml --dry-run
backuppy run    -c /etc/backuppy/config.yml
backuppy list   -c /etc/backuppy/config.yml     # show local + remote backups
```

### Creating models from templates

backuppy ships with built-in templates so you can scaffold a new model with
one command:

```bash
backuppy new --list                              # see available templates

backuppy new my-db --template postgres           # creates /etc/backuppy/my-db.yml
backuppy new prod-files --template files
backuppy new app-mssql --template mssql-full     # MSSQL FULL backup
backuppy new app-mssql-diff --template mssql-diff
backuppy new shared/storage --template shared-storage   # for use with extends:
```

If you don't pass `--template`, backuppy guesses from the model name:

```bash
backuppy new mssql-prod      # → mssql-full template
backuppy new postgres-app    # → postgres template
backuppy new files-www       # → files template
```

Other useful flags:

```bash
backuppy new my-model -d /home/user/backups        # write somewhere else
backuppy new my-model --force                      # overwrite existing
```

Generated files have `chmod 600` because they may contain credentials. Edit
them to fill in real hostnames, passwords, and bucket names — every
placeholder is marked with `TODO` or `CHANGE-ME` comments.

### Multiple models (like Backup gem's DSL)

backuppy treats each config file as a *model*. You can have many of them —
different sources, different destinations, different rotation policies — and
run them individually, in groups, or all at once.

```
/etc/backuppy/
├── shared/
│   ├── storage.yml      # S3/WebDAV credentials shared across models
│   └── notify.yml       # Telegram bot token
├── postgres-app.yml     # model 1
├── files-www.yml        # model 2
└── mssql-full.yml       # model 3
```

Each model file uses `extends:` to pull in common settings, then defines
what's unique:

```yaml
# /etc/backuppy/postgres-app.yml
extends:
  - shared/storage.yml      # paths are relative to THIS file
  - shared/notify.yml

name: postgres-app
postgres:
  enabled: true
  databases: [app_production]
s3:
  prefix: postgres-app      # override: this model writes to its own S3 prefix
  keep_last: 7              # override: keep only last 7
```

Run individually, in groups, or list them:

```bash
backuppy models --configs-dir /etc/backuppy/    # show all models
backuppy run    --configs-dir /etc/backuppy/    # run all of them
backuppy run    -c /etc/backuppy/postgres-app.yml \
                -c /etc/backuppy/files-www.yml  # run two specific ones
```

A failure in one model doesn't stop the others — they all run, and the
process exits non-zero if any failed.

Files in `shared/` (and any file starting with `_` or `.`) are skipped by
`--configs-dir` — they're includes, not models.

Typical cron setup (root):

```cron
# FULL backup at 2 AM
0 2 * * *   /usr/local/bin/backuppy run -c /etc/backuppy/config-full.yml

# DIFFERENTIAL every other hour (MSSQL only)
0 0-1,3-23 * * *  /usr/local/bin/backuppy run -c /etc/backuppy/config-diff.yml
```

## Minimal config example

Files + gzip + local + S3:

```yaml
name: webserver

archive:
  name: files
  paths: [/etc, /var/www]
  excludes: ["*/node_modules/*"]

compression:
  method: gzip

local:
  path: /var/backups/backuppy
  keep_last: 5

s3:
  enabled: true
  bucket: my-backups
  region: eu-central-1
  prefix: webserver
  access_key_id: "AKIA..."
  secret_access_key: "..."
  keep_last: 30

verify:
  enabled: true
  method: checksum
```

See [`config.example.yml`](config.example.yml) for the full reference with
every option, including all 6 databases, all 7 storages, encryption, hooks,
splitter, throttle, and notifications.

## MSSQL setup (Linux → Windows)

To back up a Microsoft SQL Server running on Windows from a Linux box:

1. **Windows side** — create a shared folder, e.g. `D:\Backups\backuppy`, and
   grant the SQL Server service account write access to it.
2. **Linux side** — mount the share over SMB:

   ```bash
   apt install cifs-utils
   mkdir -p /mnt/mssql-backups
   echo "//windows-host/Backups /mnt/mssql-backups cifs credentials=/etc/backuppy/smb-creds,vers=3.0,uid=root,gid=root 0 0" \
     >> /etc/fstab
   mount /mnt/mssql-backups
   ```

3. **SQL Server side** — create a backup user with `BACKUP DATABASE` permission:

   ```sql
   CREATE LOGIN backup_user WITH PASSWORD = 'SECRET', CHECK_POLICY = OFF;
   USE [AppDB]; CREATE USER backup_user FOR LOGIN backup_user;
   ALTER ROLE db_backupoperator ADD MEMBER backup_user;
   ```

4. **Config** — point `mssql.remote_backup_dir` to the Windows path
   (`D:\Backups\backuppy`) and `mssql.local_mount_dir` to the same directory on
   Linux (`/mnt/mssql-backups`). backuppy issues `BACKUP DATABASE` over TCP/1433,
   then picks up the .bak file from the SMB mount and uploads it.

## Restore

backuppy doesn't currently include a `restore` command — you restore manually
because every engine has its own preferred path:

- **Files** — `tar -xzf myserver-files-*.tar.gz -C /`
- **MSSQL** — `RESTORE DATABASE [AppDB] FROM DISK = N'D:\path\AppDB-full-*.bak' WITH REPLACE`
- **PostgreSQL** (custom format) — `pg_restore -d mydb myserver-full-*.dump`
- **MySQL** — `mysql mydb < myserver-full-*.sql`
- **MongoDB** — `tar -xf alldbs-full-*.tar && mongorestore --gzip dump/`
- **Redis** — stop redis, replace `/var/lib/redis/dump.rdb`, start redis
- **SQLite** — `cp myserver-full-*.sqlite /path/to/app.db`

If the file is encrypted, decrypt first:

```bash
# gpg symmetric:
gpg --decrypt --passphrase-file /etc/backuppy/passphrase -o out.tar.gz file.tar.gz.gpg

# openssl:
openssl enc -aes-256-cbc -d -pbkdf2 -iter 100000 -pass file:/etc/backuppy/openssl-pass -in file.tar.gz.enc -out out.tar.gz
```

If the file was split, reassemble first:

```bash
cat file.tar.gz.part* > file.tar.gz
```

## Project layout

```
backuppy/
├── backuppy/                  Python package
│   ├── cli.py                 argparse entry point
│   ├── config.py              dataclasses + YAML loader
│   ├── core.py                orchestration: run, list, verify, hooks
│   ├── compress.py            gzip / bzip2 / xz / zstd
│   ├── encrypt.py             GPG symmetric/asymmetric, OpenSSL
│   ├── splitter.py            chunk large files
│   ├── throttle.py            bandwidth limiter
│   ├── notify.py              email + Telegram
│   ├── databases/             one file per engine
│   └── storages/              one file per destination
├── install.sh                 curl-pipe-bash installer
├── uninstall.sh
├── config.example.yml         exhaustive reference config
├── pyproject.toml             Python packaging, extras per backend
└── tests/                     pytest tests
```

## Requirements

- Linux (Debian 12 tested; should work on Ubuntu 22+, RHEL 9, anything systemd)
- Python 3.10+
- For MSSQL: `cifs-utils` + Microsoft ODBC Driver 17/18

External CLIs needed by some dumpers (installer takes care of these only for
MSSQL — install the rest yourself if you enable those backends):

| Backend     | Needs                                  |
|-------------|----------------------------------------|
| MSSQL       | unixodbc, msodbcsql18, cifs-utils      |
| PostgreSQL  | postgresql-client                      |
| MySQL       | default-mysql-client                   |
| MongoDB     | mongodb-database-tools (`mongodump`)   |
| Redis       | redis-tools (`redis-cli`)              |
| GPG enc.    | gnupg                                  |
| OpenSSL enc.| openssl                                |
| Compression | gzip, bzip2, xz-utils, zstd            |

## Status & support

This is a personal-use tool, AGPL-clean: MIT licensed, no telemetry, no
phone-home. Bug reports and PRs welcome — but if you need 24/7 enterprise
support, this isn't the tool.

## License

MIT — see [LICENSE](LICENSE).
