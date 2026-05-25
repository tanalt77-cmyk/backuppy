"""`backuppy migrate` — automatic config migration for v3.10.0 path change.

In v3.10.0 backuppy started auto-inserting <model_name>/ into every
destination path. To avoid duplicated path segments (e.g. Backups/pixo/pixo/),
existing configs need their *_path fields trimmed to remove the explicit
model name suffix.

This command does that automatically, with a dry-run option and confirmation.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any


# Mapping: storage section → field to inspect
STORAGE_PATHS: dict[str, str] = {
    "local": "path",
    "webdav": "remote_path",
    "s3": "prefix",
    "sftp": "remote_path",
    "dropbox": "remote_path",
    "gcs": "prefix",
    "azure": "prefix",
}


def _trim_suffix(value: str, suffix: str) -> str | None:
    """If value ends with /<suffix> or \\<suffix>, strip and return.
    Otherwise return None (= no change needed)."""
    if not value or not suffix:
        return None
    sep_patterns = [f"/{suffix}", f"\\{suffix}"]
    for sep in sep_patterns:
        if value.endswith(sep):
            new_value = value[:-len(sep)]
            # Don't return empty path — fall back to "."
            return new_value if new_value else "."
    return None


def _analyze_model(yaml_data: dict) -> tuple[str, list[tuple[str, str, str, str]]]:
    """Inspect a parsed YAML dict. Returns (model_name, changes_list).
    Each change is (section, field, old_value, new_value)."""
    model_name = yaml_data.get("name", "")
    if not model_name:
        return "", []

    changes: list[tuple[str, str, str, str]] = []
    for section, field in STORAGE_PATHS.items():
        if section not in yaml_data or not isinstance(yaml_data[section], dict):
            continue
        old_value = yaml_data[section].get(field)
        if not isinstance(old_value, str):
            continue
        new_value = _trim_suffix(old_value, model_name)
        if new_value is not None:
            changes.append((section, field, old_value, new_value))
    return model_name, changes


def _resolve_model_path(model_name_or_path: str) -> Path:
    """Like in cmd_notify — accept either path or model name."""
    p = Path(model_name_or_path)
    if p.suffix in (".yml", ".yaml") and p.exists():
        return p
    from .cli import DEFAULT_CONFIGS_DIR
    base = Path(DEFAULT_CONFIGS_DIR)
    for ext in (".yml", ".yaml"):
        candidate = base / f"{model_name_or_path}{ext}"
        if candidate.exists():
            return candidate
    raise FileNotFoundError(
        f"Model '{model_name_or_path}' not found in {DEFAULT_CONFIGS_DIR}/"
    )


def _yesno(prompt: str, default: bool = True) -> bool:
    suffix = " (Y/n)" if default else " (y/N)"
    while True:
        val = input(f"{prompt}{suffix}: ").strip().lower()
        if not val:
            return default
        if val in ("y", "yes"):
            return True
        if val in ("n", "no"):
            return False


def _migrate_one(path: Path, dry_run: bool, no_confirm: bool) -> bool:
    """Process one YAML file. Returns True if it was changed (or would be)."""
    try:
        from ruamel.yaml import YAML
    except ImportError:
        print("Error: ruamel.yaml not installed. Run: pip install ruamel.yaml",
              file=sys.stderr)
        return False

    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.indent(mapping=2, sequence=4, offset=2)
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.load(f)
    if data is None:
        return False

    model_name, changes = _analyze_model(data)
    if not changes:
        print(f"  {path.name}: already up to date (model={model_name})")
        return False

    print(f"\n  {path.name} (model={model_name}):")
    for section, field, old_value, new_value in changes:
        print(f"    {section}.{field}:")
        print(f"      old: {old_value!r}")
        print(f"      new: {new_value!r}")

    if dry_run:
        print(f"  [dry-run] {path.name}: would update {len(changes)} field(s)")
        return True

    if not no_confirm and not _yesno(f"\nApply changes to {path.name}?",
                                     default=True):
        print(f"  {path.name}: skipped")
        return False

    # Make backup
    backup_path = path.with_suffix(path.suffix + ".bak")
    backup_path.write_bytes(path.read_bytes())
    print(f"  backup → {backup_path.name}")

    # Apply changes
    for section, field, _old, new_value in changes:
        data[section][field] = new_value

    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f)

    # Preserve permissions
    try:
        import stat
        st = backup_path.stat()
        path.chmod(st.st_mode & 0o777)
    except Exception:
        pass

    print(f"  ✓ {path.name}: updated {len(changes)} field(s)")
    return True


def cmd_migrate(targets: list[str], dry_run: bool, all_flag: bool,
                no_confirm: bool) -> int:
    """Migrate one or more models. `targets` may be names, paths, or empty
    (with --all)."""

    paths: list[Path] = []

    if targets:
        for t in targets:
            try:
                paths.append(_resolve_model_path(t))
            except FileNotFoundError as e:
                print(f"Error: {e}", file=sys.stderr)
                return 2
    elif all_flag:
        from .cli import DEFAULT_CONFIGS_DIR
        base = Path(DEFAULT_CONFIGS_DIR)
        if not base.is_dir():
            print(f"Error: {base} does not exist", file=sys.stderr)
            return 2
        for p in sorted(base.iterdir()):
            if (p.is_file() and p.suffix in (".yml", ".yaml") and
                not p.name.startswith(("_", ".", "config.example"))):
                paths.append(p)
    else:
        print("Error: give a model name (or use --all)", file=sys.stderr)
        return 2

    if not paths:
        print("No models to migrate.", file=sys.stderr)
        return 2

    if dry_run:
        print(f"=== Dry-run: analyzing {len(paths)} model(s) ===")
    else:
        print(f"=== Migrating {len(paths)} model(s) ===")

    changed = 0
    for p in paths:
        if _migrate_one(p, dry_run, no_confirm):
            changed += 1

    print(f"\n{'Would change' if dry_run else 'Changed'}: {changed}/{len(paths)} model(s)")
    if changed and not dry_run:
        print(f"\nBackup files saved as <model>.yml.bak — delete after verifying.")
    return 0
