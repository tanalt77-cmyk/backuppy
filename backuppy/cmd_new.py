"""Implementation of `backuppy new` — generate a model from a template."""
from __future__ import annotations

import sys
from importlib import resources
from pathlib import Path


# Available templates. Keys are the values for --template.
TEMPLATES = {
    "files": "Filesystem archive (paths + excludes)",
    "postgres": "PostgreSQL via pg_dump",
    "mysql": "MySQL/MariaDB via mysqldump",
    "mssql-full": "Windows SQL Server FULL backup",
    "mssql-diff": "Windows SQL Server DIFFERENTIAL backup",
    "mssql-log": "Windows SQL Server TRANSACTION LOG backup (requires FULL recovery)",
    "shared-storage": "Shared storage credentials (for use with extends:)",
}

# Picked when --template not given. Matches names users typically type.
# (Logic now in _detect_template — order-sensitive.)


def cmd_new(name: str, template: str | None, dest_dir: Path,
            force: bool) -> int:
    """Create a new model YAML from a template.

    name: model name (becomes 'name:' in the YAML and the filename)
    template: template key from TEMPLATES, or None to auto-detect from name
    dest_dir: where to write the file (default: /etc/backuppy)
    force: overwrite existing file
    """
    # Resolve template
    if template is None:
        template = _detect_template(name)

    if template not in TEMPLATES:
        print(f"Unknown template: {template}", file=sys.stderr)
        print(f"Available: {', '.join(sorted(TEMPLATES))}", file=sys.stderr)
        return 2

    # Read template content
    try:
        content = resources.files("backuppy.templates").joinpath(
            f"{template}.yml"
        ).read_text(encoding="utf-8")
    except Exception as e:
        print(f"Failed to read template '{template}': {e}", file=sys.stderr)
        return 2

    # Substitute placeholder
    content = content.replace("{{NAME}}", name)

    # Resolve output path
    # Allow 'shared/storage' style names → creates dest_dir/shared/storage.yml
    if "/" in name:
        out_path = (dest_dir / name).with_suffix(".yml")
    else:
        out_path = dest_dir / f"{name}.yml"

    if out_path.exists() and not force:
        print(f"File already exists: {out_path}", file=sys.stderr)
        print(f"Use --force to overwrite.", file=sys.stderr)
        return 1

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(content, encoding="utf-8")
    out_path.chmod(0o600)  # may contain credentials, lock down right away

    print(f"✓ Created {out_path}")
    print(f"  template: {template}")
    print(f"")
    print(f"Next:")
    print(f"  1. Edit the file and replace TODO/CHANGE-ME placeholders:")
    print(f"     nano {out_path}")
    print(f"  2. Verify: backuppy verify -c {out_path}")
    print(f"  3. Dry-run: backuppy run -c {out_path} --dry-run")
    return 0


def cmd_list_templates() -> int:
    """List available templates."""
    print("Available templates:")
    for key in sorted(TEMPLATES):
        print(f"  {key:<18} {TEMPLATES[key]}")
    print()
    print("Usage:")
    print("  backuppy new <name> --template <key>")
    print("  backuppy new postgres-prod --template postgres")
    return 0


def _detect_template(name: str) -> str:
    """Guess template from model name. Falls back to 'files'.

    Tries specific hints first (mssql, postgres, mysql) before generic ones (sql).
    """
    lower = name.lower()
    # Order matters: more specific keywords first to avoid 'sql' matching 'mysql'.
    specific = [
        ("mssql", "mssql-full"),
        ("postgresql", "postgres"),
        ("postgres", "postgres"),
        ("mariadb", "mysql"),
        ("mysql", "mysql"),
        ("shared", "shared-storage"),
        ("archive", "files"),
        ("files", "files"),
        ("file", "files"),
        ("pg", "postgres"),
    ]
    for key, template in specific:
        if key in lower:
            return template
    # Generic fallbacks last
    if "sql" in lower:
        return "mssql-full"
    return "files"
