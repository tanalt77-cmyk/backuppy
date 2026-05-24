#!/bin/bash
# backuppy installer (v2 — installs as Python package)
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/tanalt77-cmyk/backuppy/main/install.sh | bash
#
# Options (download first):
#   bash install.sh --update           # update package only, keep config
#   bash install.sh --branch dev       # install from specific branch/tag
#   bash install.sh --no-mssql         # skip MSSQL ODBC driver
#   bash install.sh --extras s3,sftp   # only install selected backends
#   bash install.sh --all              # install ALL backend extras (default)

set -euo pipefail

REPO_USER="${REPO_USER:-tanalt77-cmyk}"
REPO_NAME="${REPO_NAME:-backuppy}"
BRANCH="main"
INSTALL_MSSQL=1
EXTRAS="all"
UPDATE_MODE=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --update)    UPDATE_MODE=1; shift ;;
        --branch)    BRANCH="$2"; shift 2 ;;
        --no-mssql)  INSTALL_MSSQL=0; shift ;;
        --extras)    EXTRAS="$2"; shift 2 ;;
        --all)       EXTRAS="all"; shift ;;
        -h|--help)
            sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *) echo "Unknown argument: $1" >&2; exit 1 ;;
    esac
done

REPO_URL="${BACKUPPY_REPO:-https://raw.githubusercontent.com/${REPO_USER}/${REPO_NAME}/${BRANCH}}"
GIT_URL="https://github.com/${REPO_USER}/${REPO_NAME}.git"

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
    echo "Run as root." >&2
    exit 1
fi

log() { echo -e "\033[1;32m==>\033[0m $*"; }
warn() { echo -e "\033[1;33m!!\033[0m $*"; }

# Update-only mode
if [[ $UPDATE_MODE -eq 1 ]]; then
    log "Update mode"
    if [[ ! -d /opt/backuppy/venv ]]; then
        echo "ERR: /opt/backuppy/venv not found. Run full install first." >&2
        exit 1
    fi
    /opt/backuppy/venv/bin/pip install --upgrade --force-reinstall \
        "backuppy @ git+${GIT_URL}@${BRANCH}" --quiet
    log "✓ Updated from $BRANCH"
    /opt/backuppy/venv/bin/backuppy --version
    exit 0
fi

# Full install
log "[1/5] System packages"
sed -i '/^deb cdrom:/s/^/#/' /etc/apt/sources.list 2>/dev/null || true
apt update
apt install -y python3 python3-pip python3-venv git \
    gpg unixodbc unixodbc-dev cifs-utils curl gnupg ca-certificates \
    gcc python3-dev

if [[ $INSTALL_MSSQL -eq 1 ]]; then
    log "[2/5] Microsoft ODBC Driver 18"
    if [[ ! -f /usr/share/keyrings/microsoft.gpg ]]; then
        curl -fsSL https://packages.microsoft.com/keys/microsoft.asc | \
            gpg --dearmor -o /usr/share/keyrings/microsoft.gpg
    fi
    echo "deb [signed-by=/usr/share/keyrings/microsoft.gpg] https://packages.microsoft.com/debian/12/prod bookworm main" \
        > /etc/apt/sources.list.d/mssql-release.list
    apt update
    ACCEPT_EULA=Y apt install -y msodbcsql18
else
    log "[2/5] Skipping MSSQL driver (--no-mssql)"
fi

log "[3/5] Directories"
mkdir -p /opt/backuppy /etc/backuppy /var/backups/backuppy /mnt/mssql-backups

log "[4/5] Python venv and package installation"
if [[ ! -d /opt/backuppy/venv ]]; then
    python3 -m venv /opt/backuppy/venv
fi
/opt/backuppy/venv/bin/pip install --upgrade pip --quiet
/opt/backuppy/venv/bin/pip install --quiet "backuppy[${EXTRAS}] @ git+${GIT_URL}@${BRANCH}"

log "[5/5] Wrapper /usr/local/bin/backuppy"
cat > /usr/local/bin/backuppy <<'WRAP'
#!/bin/sh
exec /opt/backuppy/venv/bin/backuppy "$@"
WRAP
chmod +x /usr/local/bin/backuppy

if [[ ! -f /etc/backuppy/config.yml ]]; then
    curl -fsSL "${REPO_URL}/config.example.yml" -o /etc/backuppy/config.yml
    chmod 600 /etc/backuppy/config.yml
    log "    created /etc/backuppy/config.yml — edit it"
else
    curl -fsSL "${REPO_URL}/config.example.yml" -o /etc/backuppy/config.example.yml
    warn "    /etc/backuppy/config.yml exists; latest example saved as config.example.yml"
fi

echo ""
log "✓ Installed."
echo "  Branch:   $BRANCH"
echo "  Extras:   $EXTRAS"
echo "  Config:   /etc/backuppy/config.yml"
echo "  Wrapper:  /usr/local/bin/backuppy"
echo ""
backuppy --version
echo ""
echo "Next:"
echo "  1. Edit /etc/backuppy/config.yml"
echo "  2. backuppy verify -c /etc/backuppy/config.yml"
echo "  3. backuppy run -c /etc/backuppy/config.yml --dry-run"
