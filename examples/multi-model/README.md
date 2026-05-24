# Multi-model example

This directory shows a real-world setup with 4 backup models sharing common
credentials. Copy to `/etc/backuppy/` and edit the credentials:

```
/etc/backuppy/
├── shared/
│   ├── storage.yml      # S3 credentials, log, verify policy
│   └── notify.yml       # Telegram bot token + chat ID
├── postgres-app.yml     # Daily PostgreSQL backup
├── files-www.yml        # Daily files archive
├── mssql-full.yml       # Daily FULL MSSQL backup
└── mssql-diff.yml       # Hourly DIFFERENTIAL MSSQL backup
```

## How it works

Each model's `extends:` pulls in the shared files; the model file then defines
sources and may override any shared setting (e.g. `s3.keep_last`, `s3.prefix`).
The result is no duplication of credentials and one place to rotate keys.

## Commands

```bash
# List discovered models:
backuppy models --configs-dir /etc/backuppy/

# Run all models in sequence (one failure doesn't stop the others):
backuppy run --configs-dir /etc/backuppy/

# Run specific models:
backuppy run -c /etc/backuppy/postgres-app.yml -c /etc/backuppy/files-www.yml

# Verify (preflight check) all models:
backuppy verify --configs-dir /etc/backuppy/

# List backed-up files per model:
backuppy list -c /etc/backuppy/postgres-app.yml
```

## Cron schedule

```cron
# At top of crontab:
PATH=/usr/local/bin:/usr/bin:/bin
MAILTO=admin@example.com

# Daily 02:00: MSSQL FULL
0 2 * * *     /usr/local/bin/backuppy run -c /etc/backuppy/mssql-full.yml

# Daily 03:00: PostgreSQL + files
0 3 * * *     /usr/local/bin/backuppy run -c /etc/backuppy/postgres-app.yml -c /etc/backuppy/files-www.yml

# Hourly MSSQL DIFFERENTIAL (every hour except 02:00 when FULL runs)
0 0-1,3-23 * * *   /usr/local/bin/backuppy run -c /etc/backuppy/mssql-diff.yml
```

Or with `--configs-dir` if you want everything to run together at one time:

```cron
# All models at 02:00 daily
0 2 * * *     /usr/local/bin/backuppy run --configs-dir /etc/backuppy/
```

Just note that `--configs-dir` will also run `mssql-diff.yml` at 02:00, which
is fine — DIFFERENTIAL backups stack on the latest FULL.

## Skipped files

When using `--configs-dir`, these are automatically skipped:

- Files starting with `_` or `.` (use this to disable a model: `mv x.yml _x.yml`)
- The literal filename `config.example.yml`
- Anything in subdirectories (`shared/`, `archive/`, etc.) — `--configs-dir` is
  non-recursive
