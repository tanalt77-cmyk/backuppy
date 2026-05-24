#!/bin/bash
# backuppy uninstaller
#
# Usage:
#   bash uninstall.sh           # remove backuppy, keep config and backups
#   bash uninstall.sh --purge   # remove EVERYTHING including config and backups

set -euo pipefail

PURGE=0
[[ "${1:-}" == "--purge" ]] && PURGE=1

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
    echo "Run as root." >&2
    exit 1
fi

echo "==> Removing backuppy..."
rm -f /usr/local/bin/backuppy
rm -rf /opt/backuppy

if [[ $PURGE -eq 1 ]]; then
    echo "==> --purge: removing config, backups, and logs"
    rm -rf /etc/backuppy
    rm -rf /var/backups/backuppy
    rm -f /var/log/backuppy.log*
    echo "==> The SMB mount and MSSQL driver are NOT removed — remove manually if needed:"
    echo "    systemctl disable --now 'mnt-mssql\\x2dbackups.mount'"
    echo "    rm /etc/systemd/system/'mnt-mssql\\x2dbackups.mount'"
    echo "    apt remove msodbcsql18"
else
    echo ""
    echo "✓ Uninstalled. The following are kept (use --purge to remove):"
    echo "    /etc/backuppy/         (config files)"
    echo "    /var/backups/backuppy/ (local backups)"
    echo "    /var/log/backuppy.log*"
fi
