# Installation guide

This guide covers full installation on a fresh Debian 12 server and the
specifics of backing up Windows SQL Server databases over the network.

For a quick install summary, see the [README](README.md).

## 1. Prerequisites

- Debian 12 (Bookworm) — Ubuntu 22.04+ and RHEL 9 should work too
- Python 3.10+ (Debian 12 ships 3.11)
- Root access (or sudo)
- ~200 MB free disk space for the venv and dependencies

## 2. One-line install

```bash
curl -fsSL https://raw.githubusercontent.com/tanalt77-cmyk/backuppy/main/install.sh | bash
```

What this does:

1. `apt install` Python, git, gpg, cifs-utils, unixodbc, build tools
2. Adds the Microsoft package repo and installs `msodbcsql18`
3. Creates `/opt/backuppy/venv` (Python virtual environment)
4. Runs `pip install "git+https://github.com/tanalt77-cmyk/backuppy.git#egg=backuppy[all]"`
5. Creates a wrapper script at `/usr/local/bin/backuppy`
6. Drops a starter config at `/etc/backuppy/config.yml`

### Slimmer installs

```bash
# Skip MSSQL ODBC driver (you don't need to back up Windows SQL Server):
bash <(curl -fsSL .../install.sh) --no-mssql --extras s3,sftp

# Pick only the storage backends you'll actually use:
# Available extras: s3, mssql, sftp, dropbox, gcs, azure, all
bash <(curl -fsSL .../install.sh) --extras s3,gcs

# Install from a different branch (for testing):
bash <(curl -fsSL .../install.sh) --branch dev
```

### Updating

```bash
curl -fsSL https://raw.githubusercontent.com/tanalt77-cmyk/backuppy/main/install.sh | bash -s -- --update
```

This re-runs `pip install --upgrade` with the latest code from `main`,
keeps your `/etc/backuppy/config.yml` intact, and saves the latest
`config.example.yml` next to it for diffing.

## 3. Verify install

```bash
backuppy --version
backuppy --help
```

## 4. Configure

Edit `/etc/backuppy/config.yml`. By default everything is disabled — enable
only what you need:

```bash
nano /etc/backuppy/config.yml
chmod 600 /etc/backuppy/config.yml
```

Always run preflight first:

```bash
backuppy verify -c /etc/backuppy/config.yml
```

This checks: config syntax, local storage write access, each remote storage
credentials, each database connection, and encryption material existence.
**Fix every error from `verify` before you run the actual backup.**

## 5. Back up a Windows SQL Server

This is the trickiest setup. SQL Server's `BACKUP DATABASE` writes to a
Windows-local path; backuppy reads it via SMB.

### 5.1 Windows side

1. Create the backup directory: `D:\Backups\backuppy`
2. Share it (e.g. as `\\windows-host\Backups`) with read/write to a backup user
3. Grant the **SQL Server service account** write access to `D:\Backups\backuppy`
   (Right-click folder → Properties → Security)
4. Create a SQL login with minimal privileges:

   ```sql
   CREATE LOGIN backup_user WITH PASSWORD = 'STRONG-PASSWORD', CHECK_POLICY = OFF;

   USE [AppDB];
   CREATE USER backup_user FOR LOGIN backup_user;
   ALTER ROLE db_backupoperator ADD MEMBER backup_user;

   -- Repeat for each database to back up
   USE [OtherDB];
   CREATE USER backup_user FOR LOGIN backup_user;
   ALTER ROLE db_backupoperator ADD MEMBER backup_user;
   ```

5. Confirm TCP/1433 is reachable from your Linux box:

   ```bash
   nc -zv windows-host 1433
   ```

### 5.2 Linux side: mount the SMB share

```bash
# Credentials file (chmod 600!)
cat > /etc/backuppy/smb-creds <<EOF
username=backup_user
password=SHARE-PASSWORD
domain=WORKGROUP
EOF
chmod 600 /etc/backuppy/smb-creds

# Persistent mount via /etc/fstab
mkdir -p /mnt/mssql-backups
echo "//windows-host/Backups /mnt/mssql-backups cifs credentials=/etc/backuppy/smb-creds,vers=3.0,uid=root,gid=root,iocharset=utf8,nofail 0 0" \
  >> /etc/fstab
mount /mnt/mssql-backups

# Test
touch /mnt/mssql-backups/.probe && rm /mnt/mssql-backups/.probe && echo "SMB OK"
```

If `mount` hangs, the share isn't reachable or credentials are wrong — check
`dmesg | tail` for CIFS errors.

#### systemd-based auto-mount (preferred over fstab for reliability)

Create `/etc/systemd/system/mnt-mssql\x2dbackups.mount` (the `\x2d` is a
literal backslash-x-2-d, systemd's escape for `-`):

```ini
[Unit]
Description=Mount Windows SMB backup share
After=network-online.target
Wants=network-online.target

[Mount]
What=//windows-host/Backups
Where=/mnt/mssql-backups
Type=cifs
Options=credentials=/etc/backuppy/smb-creds,vers=3.0,uid=root,gid=root,iocharset=utf8,nofail
TimeoutSec=30

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload
systemctl enable --now 'mnt-mssql\x2dbackups.mount'
```

### 5.3 Configure backuppy for MSSQL

In `/etc/backuppy/config.yml`:

```yaml
mssql:
  enabled: true
  host: "windows-host"            # or IP
  port: 1433
  username: "backup_user"
  password: "STRONG-PASSWORD"
  remote_backup_dir: "D:\\Backups\\backuppy"   # double backslashes!
  local_mount_dir: "/mnt/mssql-backups"        # same files, Linux view
  compression: true
  checksum: true
  cleanup_remote: true             # delete .bak from Windows after Linux picks it up
  databases:
    - { name: AppDB,   backup_type: FULL }
    - { name: OtherDB, backup_type: FULL }
```

Run preflight: `backuppy verify -c /etc/backuppy/config.yml`

You should see `MSSQL OK: ...` and `SMB mount OK: /mnt/mssql-backups`.

## 6. FULL + DIFFERENTIAL strategy (MSSQL hourly backups)

For Windows SQL Server you can take a small DIFFERENTIAL backup every hour
and a single FULL backup every night. Use **two config files**:

`/etc/backuppy/config-full.yml`:
```yaml
mssql:
  databases:
    - { name: AppDB,   backup_type: FULL }
    - { name: OtherDB, backup_type: FULL }
```

`/etc/backuppy/config-diff.yml`:
```yaml
mssql:
  databases:
    - { name: AppDB,   backup_type: DIFFERENTIAL }
    - { name: OtherDB, backup_type: DIFFERENTIAL }
```

Crontab:
```cron
# FULL at 02:00
0 2 * * *   /usr/local/bin/backuppy run -c /etc/backuppy/config-full.yml >> /var/log/backuppy-full.log 2>&1

# DIFFERENTIAL every hour except 02:00 (when FULL runs)
0 0-1,3-23 * * *   /usr/local/bin/backuppy run -c /etc/backuppy/config-diff.yml >> /var/log/backuppy-diff.log 2>&1
```

To **restore at point T**, apply the latest FULL before T, then the
most recent DIFFERENTIAL between FULL and T (one DIFF replays all
changes since the FULL).

## 7. Hetzner Storage Share (managed Nextcloud)

Hetzner Storage Share is managed Nextcloud reachable only via WebDAV.

1. Sign in to your Hetzner Storage Share web UI
2. Settings → Security → "Generate app password"
3. Use the **app password** (not your account password) in the config:

```yaml
webdav:
  enabled: true
  base_url: "https://uXXXXXX.your-storageshare.de/remote.php/dav/files/uXXXXXX/"
  remote_path: "Backups/myserver"
  username: "uXXXXXX"
  password: "APP-PASSWORD"
  keep_last: 30
```

The base_url format is fixed — copy `uXXXXXX` from your account page.

## 8. Backblaze B2 (cheap S3-compatible)

```yaml
s3:
  enabled: true
  bucket: "my-b2-bucket"
  region: "eu-central-003"           # whatever your B2 region is
  prefix: "myserver"
  access_key_id: "keyID"             # from B2 application keys
  secret_access_key: "applicationKey"
  endpoint_url: "https://s3.eu-central-003.backblazeb2.com"
  storage_class: "STANDARD"
  keep_last: 30
```

B2 charges ~$5/TB/month — significantly cheaper than S3 Standard.

## 9. Encryption

### GPG symmetric (single passphrase)

```bash
echo -n "long-strong-passphrase" > /etc/backuppy/passphrase
chmod 600 /etc/backuppy/passphrase
```

```yaml
encryption:
  enabled: true
  method: gpg-symmetric
  passphrase_file: /etc/backuppy/passphrase
```

### GPG asymmetric (encrypt-only on backup host)

This is more secure: the backup host has only the **public key**, so even a
compromise of the backup server can't decrypt past backups.

On your laptop/workstation:
```bash
gpg --full-generate-key      # type 1 (RSA), 4096 bits, no expiry
gpg --export -a "backup@example.com" > recipient.pub
```

Transfer `recipient.pub` to the backup server:
```bash
scp recipient.pub root@server:/tmp/
ssh root@server 'gpg --import /tmp/recipient.pub && rm /tmp/recipient.pub'
```

```yaml
encryption:
  enabled: true
  method: gpg-asymmetric
  recipient: "backup@example.com"   # or the fingerprint
```

To restore, you must have the private key on your laptop:
```bash
gpg --decrypt -o file.tar.gz file.tar.gz.gpg
```

## 10. Troubleshooting

**`SMB mount OK` but backup fails with "file did not appear via SMB"** — the
SQL Server service account doesn't have write access to the Windows folder.
Right-click the folder → Properties → Security → grant the service account
"Modify".

**`pyodbc.Error: ... SSL handshake failed`** — older SQL Server (2017 and
below) doesn't speak the newer TLS. Add `trust_server_certificate: true` to
your `mssql:` section (it's the default in `config.example.yml`).

**`MKCOL ... 404` on WebDAV** — the Hetzner `base_url` is wrong. It must end
with `/remote.php/dav/files/uXXXXXX/` including the trailing slash.

**`backuppy: command not found`** — `/usr/local/bin` not in PATH. Either run
`/opt/backuppy/venv/bin/backuppy` directly, or add `/usr/local/bin` to PATH.

**Logs are noisy** — set `log.level: WARNING` in the config.

**Cron jobs run with empty PATH** — always put `PATH=/usr/local/bin:/usr/bin:/bin`
at the top of the crontab.

## 11. Uninstall

```bash
curl -fsSL https://raw.githubusercontent.com/tanalt77-cmyk/backuppy/main/uninstall.sh | bash
```

This removes the venv, wrapper, and **leaves your config and backups intact**.
Delete `/etc/backuppy/`, `/var/backups/backuppy/`, and `/mnt/mssql-backups/`
manually if you want a full wipe.
