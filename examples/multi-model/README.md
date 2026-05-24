# Multi-model example (v3.0)

Three backup models that share Nextcloud credentials via `extends:`:

- `postgres-app.yml` — daily PostgreSQL dump
- `files-www.yml` — daily files archive
- `mssql-full.yml` — daily MSSQL FULL backup from Windows server

## Setup

1. Copy `shared/storage.yml` → `/etc/backuppy/shared/storage.yml`
2. Replace `USER` and `APP-PASSWORD` with your actual Nextcloud credentials
3. Copy each model file to `/etc/backuppy/` and fill in TODOs
4. Run `backuppy verify --configs-dir /etc/backuppy/` to preflight
5. Add to cron

## Commands

```bash
backuppy models --configs-dir /etc/backuppy/    # what models are configured
backuppy verify --configs-dir /etc/backuppy/    # preflight all
backuppy run    --configs-dir /etc/backuppy/    # run all
```
