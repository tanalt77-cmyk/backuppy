# backuppy

A modern Python replacement for the unmaintained Ruby `backup` gem.
Backups for MSSQL, PostgreSQL, MySQL, MongoDB, Redis, SQLite — plus arbitrary
files — uploaded to local storage, Nextcloud (WebDAV), S3, SFTP, Dropbox,
GCS, or Azure Blob.

Tested on Debian 12. Should work on any Linux with Python 3.11+.

## Architecture

Each YAML file in `/etc/backuppy/` is a **model** — one independent backup
job. A model is composed of three building blocks:

- **Triggers** (optional) — things that *produce* files. E.g. an MSSQL
  trigger runs `BACKUP DATABASE` over TCP, leaving a `.bak` file somewhere
  on the SQL Server side that you've made visible to Linux via SMB.
- **Sources** (required) — things that *pick up* files for backup. The
  `files` source matches paths (with globs), packs them into a tar, and
  hands the result to the pipeline.
- **Destinations** — where backups are stored. `local` is filesystem;
  `webdav`/`s3`/`sftp`/`dropbox`/`gcs`/`azure` are remote.

The pipeline runs triggers → sources → optional compression/encryption →
upload to all enabled destinations → rotation (delete old backups).

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
```

## Updating

To update an existing install to the latest version:

```bash
curl -fsSL https://raw.githubusercontent.com/tanalt77-cmyk/backuppy/main/install.sh | bash -s -- --update
```

Verify:

```bash
backuppy --version
```

## CLI commands

| Command | What it does |
|---|---|
| `backuppy run <model>` | Run a backup |
| `backuppy run <m1> <m2>` | Run several models in sequence |
| `backuppy run --all` | Run every model in `/etc/backuppy/` |
| `backuppy verify <model>` | Preflight: check config, connectivity, credentials — without writing data |
| `backuppy list <model>` | Show stored backups in every destination |
| `backuppy models` | List all discovered models |
| `backuppy new <name>` | Create a model from a template |
| `backuppy notify add <model>` | Interactive wizard to add an email/telegram block |
| `backuppy notify test <model>` | Send a one-shot test notification |
| `backuppy migrate <model>` | Auto-update old `*_path` fields for the v3.10 model-name-prefix change |
| `backuppy --version` | Show version |

By name (looks in `/etc/backuppy/`):
```bash
backuppy run pixo
backuppy verify pixo
```

By explicit path:
```bash
backuppy run -c /path/to/some.yml
backuppy run -c some.yml --dry-run
```

## Creating models

The fastest way to start: generate a model from a template.

```bash
backuppy new --list                          # see available templates
backuppy new myserver --template mssql-full  # create /etc/backuppy/myserver.yml
backuppy new app-files --template files
backuppy new pg-prod --template postgres
```

Generated files include comments. Edit afterwards:

```bash
nano /etc/backuppy/myserver.yml        # fill in TODO placeholders
chmod 600 /etc/backuppy/myserver.yml   # protect credentials
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

Override the destination directory with `--dir`:

```bash
backuppy new myserver --template mssql-full --dir /tmp/configs/
```

## Examples

### Just files (no trigger)

Files already exist on disk — pack, compress, upload.

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
  path: /var/backups/backuppy
  keep_last: 7
webdav:
  enabled: true
  base_url: "https://nextcloud.example.com/remote.php/dav/files/USER/"
  remote_path: "Backups"
  username: USER
  password: APP-PASSWORD
  keep_last: 30
```

Each model name is automatically appended to every destination path —
files go into `Backups/web-content/...` in the example above. The same
applies to local storage (`/var/backups/backuppy/web-content/...`).

### Folder structure inside the tar archive

When `archive_name` is set, backuppy packs everything into a single tar.
The folder structure inside the archive depends on what you put in `paths`:

| `paths:` entry            | What you get inside the tar           |
|---------------------------|---------------------------------------|
| `/mnt/data/Reports`       | `Reports/` (with all subdirs/files)   |
| `/mnt/data/*`             | `Reports/...`, `Invoices/...`, etc.   |
| `/mnt/data/Reports/**/*`  | files relative to `Reports/`          |
| `/etc/myfile.conf`        | `myfile.conf` (single file)           |

Example: back up two folders from an SMB-mounted share, preserving the
folder structure inside the archive:

```yaml
name: file-bases
group_by_run: true

sources:
  - type: files
    paths:
      - /mnt/share/Reports
      - /mnt/share/Invoices
    excludes:
      - "*.tmp"
      - "*.lock"
    archive_name: data
    delete_after_pickup: false       # source files are someone else's data

local:
  enabled: true
  path: /var/backups/backuppy
  keep_last: 7

webdav:
  enabled: true
  base_url: "https://nextcloud.example.com/remote.php/dav/files/USER/"
  remote_path: "Backups"
  username: USER
  password: APP-PASSWORD
  keep_last: 30
```

Result inside the tar:

```
file-bases-data-20260608-104500.tar
├── Reports/
│   ├── 2025-Q4.pdf
│   └── subfolder/...
└── Invoices/
    └── jan.xlsx
```

### MSSQL on Windows → Nextcloud

The trigger runs `BACKUP DATABASE` over TCP/1433. SQL Server writes the
.bak to a Windows path. You arrange (via SMB mount) for that path to
appear as `/mnt/win-server1/backuppy` on Linux. backuppy doesn't care
how — it just looks at the Linux path.

```yaml
name: mssql-prod

triggers:
  - type: mssql
    host: "10.0.0.5"
    port: 1433
    username: "backup_user"
    password: "SECRET"
    output_dir_windows: "D:\\Backups\\backuppy"
    databases:
      - { name: AppDB, backup_type: FULL }
      - { name: OtherDB, backup_type: FULL }
    compression: true
    checksum: true
    copy_only: false

sources:
  - type: files
    paths:
      - "/mnt/win-server1/backuppy/*.bak"
    delete_after_pickup: true

compression:
  method: none                       # MSSQL is already compressed

local:
  enabled: true
  path: /var/backups/backuppy
  keep_last: 7

webdav:
  enabled: true
  base_url: "https://nextcloud.example.com/remote.php/dav/files/USER/"
  remote_path: "Backups"
  username: USER
  password: APP-PASSWORD
  keep_last: 30

verify:
  enabled: true
  method: size

log:
  file: /var/log/backuppy.log
  level: INFO
```

### MSSQL DIFFERENTIAL and TRANSACTION LOG backups

A complete point-in-time-recovery strategy uses three backup types:

| Type            | Suffix in filename | File extension | Schedule       | Keeps      |
|-----------------|-------------------|----------------|----------------|------------|
| FULL            | `-full-`          | `.bak`         | Daily          | 7 days     |
| DIFFERENTIAL    | `-diff-`          | `.bak`         | 1-2× per day   | 7 days     |
| LOG             | `-log-`           | `.trn`         | Hourly         | 24-48      |

LOG backups require the database's `recovery_model` to be `FULL` or
`BULK_LOGGED`. backuppy's `verify` will fail with a clear error if you
ask for LOG on a database in `SIMPLE` recovery model.

```yaml
# myserver-log.yml — hourly transaction log backups
name: myserver-log

triggers:
  - type: mssql
    host: "10.0.0.5"
    username: backup_user
    password: SECRET
    output_dir_windows: "D:\\Backups\\backuppy"
    databases:
      - { name: AppDB, backup_type: LOG }     # produces .trn files
    compression: true

sources:
  - type: files
    paths: ["/mnt/win-server1/backuppy/*.trn"]
    delete_after_pickup: false

local:
  enabled: false                              # LOG files small, skip local stage

webdav:
  enabled: true
  base_url: "https://nextcloud.example.com/remote.php/dav/files/USER/"
  remote_path: "Backups"
  username: USER
  password: APP-PASSWORD
  keep_last: 24                               # 24 hourly logs = 1 day PITR
  chunked: false                              # small files, direct PUT
```

### MSSQL with single local copy (static names + timestamped upload)

By default, every MSSQL backup creates a unique file like
`AppDB-full-20260525-021713.bak`. These accumulate on the Windows side
and need cleanup (`delete_after_pickup: true` or manual rotation).

Alternative: tell SQL Server to write to a *static* filename like
`AppDB-full.bak` — each new backup overwrites the previous local copy.
backuppy then renames with a timestamp when uploading, so cloud history
is still complete.

```yaml
name: myserver

triggers:
  - type: mssql
    host: "10.0.0.5"
    username: backup_user
    password: SECRET
    output_dir_windows: "D:\\Backups\\backuppy"
    databases:
      - { name: AppDB, backup_type: FULL }
      - { name: OtherDB, backup_type: FULL }
    compression: true
    static_local_name: true              # SQL writes AppDB-full.bak (no timestamp)

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
  remote_path: "Backups"
  username: USER
  password: APP-PASSWORD
  keep_last: 7
```

Result:
- **On Windows**: only `AppDB-full.bak`, `OtherDB-full.bak` — one fresh
  copy per DB, always up to date.
- **In Nextcloud**: `AppDB-full-20260525-021713.bak` history, last 7 kept.

### Group by run (one folder per backup run)

Set `group_by_run: true` to make backuppy create a per-run subdirectory
named `YYYYMMDD-HHMMSS` inside every destination, and put all files of
that run inside it. Rotation then keeps the last N **folders** instead
of the last N files-per-prefix.

```yaml
name: myserver
group_by_run: true              # all destinations get per-run subdir

triggers:
  - type: mssql
    host: "10.0.0.5"
    username: backup_user
    password: SECRET
    output_dir_windows: "D:\\Backups\\backuppy"
    databases:
      - { name: AppDB, backup_type: FULL }
      - { name: OtherDB, backup_type: FULL }
    static_local_name: true     # SQL writes AppDB-full.bak (no timestamp)

sources:
  - type: files
    paths: ["/mnt/win-server1/backuppy/*.bak"]

local:
  enabled: true
  path: /var/backups/backuppy
  keep_last: 7                  # keep 7 run-folders

webdav:
  enabled: true
  base_url: "https://nextcloud.example.com/remote.php/dav/files/USER/"
  remote_path: "Backups"
  username: USER
  password: APP-PASSWORD
  keep_last: 7                  # keep 7 run-folders
```

Result in Nextcloud after a week:

```
Backups/myserver/
├── 20260518-021713/
│   ├── AppDB-full.bak
│   └── OtherDB-full.bak
├── 20260519-021713/
│   └── ...
├── 20260520-021713/
└── 20260524-021713/            # 7 most recent kept
```

Each folder is a complete snapshot of all databases at that moment.

### Custom temporary directory

By default backuppy uses the system temp directory (`/tmp` on most
systems). If `/tmp` is on tmpfs (in RAM) or too small for your backups,
set `tmp_dir` to a path on a larger disk. The directory is created
automatically.

```yaml
name: myserver
tmp_dir: /var/tmp/backuppy         # writable path on a large disk
```

A common case: SQL Server databases of 10+ GB combined — tmpfs `/tmp`
will run out of memory partway through. `tmp_dir: /var/tmp/backuppy`
puts work files on the regular disk.

### Custom hook trigger

If you need to run something arbitrary before pickup — for example,
trigger a custom export from an application — use a `hook` trigger:

```yaml
triggers:
  - type: hook
    command: "/usr/local/bin/export-data.sh"
    # If command exits non-zero, backuppy aborts the run.

sources:
  - type: files
    paths: ["/var/exports/*.sql"]
    delete_after_pickup: true
```

## Multiple models

backuppy treats each YAML file as a model. Run individually, in groups,
or all at once:

```bash
backuppy run pixo                          # one model by name
backuppy run pixo files-www mssql-prod     # multiple
backuppy run --all                         # everything in /etc/backuppy/
```

Useful when you have one Linux backup-server serving multiple Windows
SQL Servers — one model file per server/database group.

## Triggers

Backuppy comes with these built-in triggers:

| Trigger     | Produces                          | Config fields (key ones)                    |
|-------------|-----------------------------------|---------------------------------------------|
| `mssql`     | `.bak` / `.trn` via BACKUP        | `host`, `username`, `password`, `output_dir_windows`, `databases`, `static_local_name`, `compression`, `checksum`, `copy_only` |
| `postgres`  | `.dump` / `.sql` via pg_dump      | `host`, `username`, `databases`, `format`   |
| `mysql`     | `.sql.gz` via mysqldump           | `host`, `username`, `databases`, `single_transaction` |
| `mongodb`   | mongodump output                  | `uri`, `databases`, `gzip`, `oplog`         |
| `redis`     | `.rdb` via BGSAVE                 | `host`, `port`, `password`, `rdb_path`      |
| `sqlite`    | `.db` via online backup           | `databases` (list of paths)                 |
| `hook`      | Whatever your script does         | `command`                                   |

## Sources

| Source   | What it picks up                                              |
|----------|---------------------------------------------------------------|
| `files`  | Files/dirs matching `paths:` (globs ok), optionally tarred up |

Key fields:

- `paths` — list of paths or glob patterns
- `excludes` — list of glob patterns to skip
- `archive_name` — if set, pack all matched files into one tar
- `delete_after_pickup` — if true, remove originals after successful pickup
- `rename_with_timestamp` — insert timestamp into filenames (use with mssql `static_local_name`)

## Destinations

Each model can write to any combination — same backup goes to every
enabled destination.

| Destination | Key field for path | Notes                                            |
|-------------|--------------------|--------------------------------------------------|
| `local`     | `path`             | Filesystem; mounted disks count                  |
| `webdav`    | `remote_path`      | Tested with Nextcloud + Hetzner Storage Share    |
| `s3`        | `prefix`           | AWS or any S3-compatible (MinIO, Backblaze B2…)  |
| `sftp`      | `remote_path`      | Password or key-based auth                       |
| `dropbox`   | `remote_path`      | Long-lived refresh token or short access token   |
| `gcs`       | `prefix`           | Service-account JSON                             |
| `azure`     | `prefix`           | Account key or connection string                 |

Every destination automatically gets `<model_name>/` appended to its
path/prefix. So `remote_path: "Backups"` for model `pixo` stores files
under `Backups/pixo/`.

`keep_last` controls per-destination rotation:
- Without `group_by_run`: keep last N **files** (per filename prefix).
- With `group_by_run`: keep last N **run-folders**.

### WebDAV chunked upload (for Nextcloud)

By default, WebDAV uploads use Nextcloud's chunked-upload protocol for
files above `chunked_threshold_mb`. This avoids `client_max_body_size`
errors on reverse-proxied Nextcloud setups.

```yaml
webdav:
  enabled: true
  base_url: "..."
  remote_path: "Backups"
  username: USER
  password: APP-PASSWORD
  chunked: true                      # default
  chunked_threshold_mb: 500          # files >= this use chunked upload
  chunked_chunk_size_mb: 50          # each chunk size
  chunked_retries: 3                 # retries per chunk
  timeout: 900                       # seconds per request
```

Increase `chunked_chunk_size_mb` (e.g. 1000) for faster uploads if your
Nextcloud reverse-proxy allows larger request bodies. Test with `curl`:

```bash
dd if=/dev/zero of=/tmp/test.bin bs=1M count=1500
curl -u USER:PASSWORD -T /tmp/test.bin \
  "https://nextcloud.example.com/remote.php/dav/files/USER/test.bin" \
  -w "HTTP: %{http_code} Time: %{time_total}s\n"
```

If you get HTTP 201, that chunk size is fine.

## Compression and encryption

```yaml
compression:
  method: gzip           # gzip | bzip2 | xz | zstd | none
  level: 6               # null = default; gzip/bz2/xz: 1-9; zstd: 1-22

encryption:
  enabled: false
  method: gpg-symmetric  # gpg-symmetric | gpg-asymmetric | openssl
  passphrase_file: /etc/backuppy/passphrase   # chmod 600
```

Set `compression.method: none` for MSSQL backups — SQL Server's native
`COMPRESSION` is already applied by the trigger.

## Notifications

Two channels supported: email and Telegram.

```yaml
email:
  enabled: true
  when: on_failure        # always | on_failure | on_warning | on_success | on_issue | never
  smtp_host: smtp.example.com
  smtp_port: 587
  smtp_user: backups@example.com
  smtp_password: "SECRET"
  use_tls: true
  from_addr: backups@example.com
  to_addrs:
    - admin@example.com

telegram:
  enabled: true
  when: on_failure
  bot_token: "123456:ABC-DEF..."     # from @BotFather
  chat_id: "-100123456789"           # personal chat or @channelname
```

`on_issue` = warning OR failure. `on_warning` = only warnings.

Interactive setup:

```bash
backuppy notify add myserver       # wizard guides you through email or telegram
backuppy notify test myserver      # send a one-shot test message
```

The wizard preserves YAML comments and structure (uses ruamel.yaml).

## Verify

Before-and-after sanity checks:

```yaml
verify:
  enabled: true
  method: size           # size | checksum (where backend supports it)
```

`backuppy verify <model>` runs the pre-flight: validates config, tests
trigger connections (e.g. logs into MSSQL, checks recovery_model),
checks local writability, opens each destination — all without writing
backup data.

## Cron

backuppy doesn't run itself — use cron or systemd timers. Example:

```cron
PATH=/usr/local/bin:/usr/bin:/bin
MAILTO=""

# Daily FULL at 02:17 — keep 7 days
17 2 * * *   /usr/bin/flock -n /var/lock/backuppy-myserver.lock /usr/local/bin/backuppy --no-progress run myserver >> /var/log/backuppy-cron.log 2>&1

# Hourly LOG at *:30 — keep 24
30 * * * *   /usr/bin/flock -n /var/lock/backuppy-myserver.lock /usr/local/bin/backuppy --no-progress run myserver-log >> /var/log/backuppy-cron.log 2>&1

# Weekly on Monday at 3:17
17 3 * * 1   /usr/bin/flock /var/lock/backuppy-myserver.lock /usr/local/bin/backuppy --no-progress run myserver-weekly >> /var/log/backuppy-cron.log 2>&1

# Monthly on the 1st at 4:17
17 4 1 * *   /usr/bin/flock /var/lock/backuppy-myserver.lock /usr/local/bin/backuppy --no-progress run myserver-monthly >> /var/log/backuppy-cron.log 2>&1
```

`flock -n` (with `-n`) skips the run if another backup is still holding
the lock. Drop `-n` (just `/usr/bin/flock /var/lock/...`) to wait
instead — useful for weekly/monthly which should run even if a daily
runs long.

For log rotation:

```bash
cat > /etc/logrotate.d/backuppy <<'EOF'
/var/log/backuppy-cron.log /var/log/backuppy.log {
    weekly
    rotate 4
    compress
    delaycompress
    missingok
    notifempty
    create 0644 root root
}
EOF
```

## Migration from v3.9 to v3.10

In v3.10.0 backuppy started automatically appending `<model_name>/` to
every destination path/prefix. If your existing configs explicitly
include the model name in `remote_path` (e.g. `Backups/myserver`), the
path will become `Backups/myserver/myserver/` — duplicated.

To fix automatically:

```bash
# See what would change without writing
backuppy migrate --all --dry-run

# Apply with confirmation prompts
backuppy migrate --all

# Apply non-interactively
backuppy migrate --all --yes

# Only one model
backuppy migrate myserver
```

`.bak` files are saved before any change, so you can roll back:

```bash
cp /etc/backuppy/myserver.yml.bak /etc/backuppy/myserver.yml
```

After verifying:

```bash
rm /etc/backuppy/*.yml.bak
```

## Restore

backuppy stores plain files in destinations — no special format. To
restore:

1. Download the backup from the destination (Web UI, `rclone`, `aws s3 cp`,
   `backuppy list <model>` to find paths).
2. If compressed/encrypted, decompress/decrypt with the same tool used
   in the model.
3. For MSSQL: `RESTORE DATABASE [Name] FROM DISK = N'C:\path\to\file.bak'
   WITH REPLACE` in SSMS.
4. For PostgreSQL custom-format: `pg_restore -d target_db file.dump`.
5. For files: extract the tar.

For point-in-time recovery with MSSQL (after restoring FULL → DIFF → LOG
chain), use `RESTORE ... WITH NORECOVERY` for all but the last step.

## License

MIT.
