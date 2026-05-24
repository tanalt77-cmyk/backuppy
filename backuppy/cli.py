"""Command-line interface.

Three ways to point at config files:

  1. By name (looks in DEFAULT_CONFIGS_DIR — typically /etc/backuppy/):
       backuppy run pixo
       backuppy verify pixo mssql-prod files-www
       backuppy run pixo postgres-app

  2. By explicit path (anywhere on disk):
       backuppy run -c /path/to/some.yml
       backuppy run -c ./local.yml -c /opt/other.yml

  3. All models in a directory:
       backuppy run --all
       backuppy run --configs-dir /custom/dir/
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .config import Config
from .core import setup_logger, cmd_run, cmd_list, cmd_verify, cmd_models
from .cmd_new import cmd_new, cmd_list_templates, TEMPLATES


DEFAULT_CONFIGS_DIR = "/etc/backuppy"


def _resolve_name_to_path(name: str, base_dir: Path) -> Path | None:
    """Try base_dir/<name>.yml, base_dir/<name>.yaml. Return None if not found."""
    for ext in (".yml", ".yaml"):
        p = base_dir / f"{name}{ext}"
        if p.is_file():
            return p
    return None


def _collect_configs(names: list[str], configs: list[str] | None,
                     configs_dir: str | None,
                     all_flag: bool) -> list[Path]:
    """Resolve positional names + --config + --configs-dir + --all into a path list."""
    base_dir = Path(DEFAULT_CONFIGS_DIR)
    paths: list[Path] = []

    # 1. Positional names — look up in default configs dir
    for name in names:
        # Allow "shared/storage" style → base_dir / shared / storage.yml
        if "/" in name or "\\" in name:
            print(f"Model name '{name}' contains path separator. "
                  f"Use --config / -c for paths.", file=sys.stderr)
            sys.exit(2)
        p = _resolve_name_to_path(name, base_dir)
        if p is None:
            print(f"Model '{name}' not found in {base_dir}/", file=sys.stderr)
            avail = _list_available_models(base_dir)
            if avail:
                print(f"Available: {', '.join(avail)}", file=sys.stderr)
            sys.exit(2)
        paths.append(p)

    # 2. --config (explicit paths)
    if configs:
        for c in configs:
            p = Path(c).expanduser()
            if not p.exists():
                print(f"Config not found: {p}", file=sys.stderr)
                sys.exit(2)
            paths.append(p)

    # 3. --all (default configs dir) or --configs-dir
    target_dir: Path | None = None
    if all_flag:
        target_dir = base_dir
    elif configs_dir:
        target_dir = Path(configs_dir).expanduser()

    if target_dir is not None:
        if not target_dir.is_dir():
            print(f"Not a directory: {target_dir}", file=sys.stderr)
            sys.exit(2)
        for p in sorted(target_dir.iterdir()):
            if p.is_file() and p.suffix in (".yml", ".yaml") and \
               not p.name.startswith(("_", ".", "config.example")):
                paths.append(p)

    # Deduplicate while preserving order
    seen: set[Path] = set()
    unique: list[Path] = []
    for p in paths:
        r = p.resolve()
        if r not in seen:
            seen.add(r)
            unique.append(p)

    if not unique:
        print(
            "No config(s) given. Use one of:\n"
            "  backuppy <command> <model-name>   (looks in /etc/backuppy/)\n"
            "  backuppy <command> --all          (all models in /etc/backuppy/)\n"
            "  backuppy <command> -c /path/to.yml",
            file=sys.stderr,
        )
        sys.exit(2)

    return unique


def _list_available_models(base_dir: Path) -> list[str]:
    """Return sorted list of model names available in base_dir."""
    if not base_dir.is_dir():
        return []
    out = []
    for p in sorted(base_dir.iterdir()):
        if p.is_file() and p.suffix in (".yml", ".yaml") and \
           not p.name.startswith(("_", ".", "config.example")):
            out.append(p.stem)
    return out


def _add_target_args(parser: argparse.ArgumentParser) -> None:
    """Args shared by run/list/verify/models — three ways to specify configs."""
    parser.add_argument("names", nargs="*",
                        help=f"Model name(s) — files in {DEFAULT_CONFIGS_DIR}/")
    parser.add_argument("--config", "-c", action="append",
                        help="Explicit path to a YAML (repeatable).")
    parser.add_argument("--configs-dir",
                        help="Custom directory of *.yml/*.yaml configs.")
    parser.add_argument("--all", action="store_true",
                        help=f"All models in {DEFAULT_CONFIGS_DIR}/")


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="backuppy",
        description="Modular backup tool inspired by Backup gem."
    )
    ap.add_argument("--version", action="version",
                    version=f"backuppy {__version__}")

    sub = ap.add_subparsers(dest="command", required=True,
                            metavar="COMMAND")

    # ---- run ----
    p_run = sub.add_parser("run", help="Run one or more backup models")
    _add_target_args(p_run)
    p_run.add_argument("--dry-run", action="store_true",
                       help="Show what would be done without doing it.")

    # ---- list ----
    p_list = sub.add_parser("list",
                            help="List backup files (local + remote) per model")
    _add_target_args(p_list)

    # ---- verify ----
    p_verify = sub.add_parser("verify",
                              help="Preflight check: configs, creds, access")
    _add_target_args(p_verify)

    # ---- models ----
    p_models = sub.add_parser("models",
                              help="List discovered models in a directory")
    _add_target_args(p_models)

    # ---- new ----
    p_new = sub.add_parser("new", help="Create a new model from a template")
    p_new.add_argument("name", nargs="?",
                       help="Model name (also becomes the filename).")
    p_new.add_argument("--template", "-t",
                       choices=sorted(TEMPLATES.keys()),
                       help="Template (auto-detected from name if omitted).")
    p_new.add_argument("--dir", "-d", default=DEFAULT_CONFIGS_DIR,
                       help=f"Where to create the file (default: {DEFAULT_CONFIGS_DIR}).")
    p_new.add_argument("--force", "-f", action="store_true",
                       help="Overwrite if the file already exists.")
    p_new.add_argument("--list", action="store_true",
                       help="List available templates and exit.")

    args = ap.parse_args()

    if args.command == "new":
        if args.list:
            return cmd_list_templates()
        if not args.name:
            print("Error: 'name' is required (or use --list to see templates).",
                  file=sys.stderr)
            return 2
        return cmd_new(args.name, args.template, Path(args.dir).expanduser(),
                       args.force)

    # For models without any target → show what's available in DEFAULT_CONFIGS_DIR
    if args.command == "models" and not (args.names or args.config or
                                          args.configs_dir or args.all):
        # Default behavior: list models in DEFAULT_CONFIGS_DIR
        args.all = True

    config_paths = _collect_configs(
        getattr(args, "names", []) or [],
        getattr(args, "config", None),
        getattr(args, "configs_dir", None),
        getattr(args, "all", False),
    )

    if args.command == "models":
        return cmd_models(config_paths)

    overall = 0
    multi = len(config_paths) > 1
    for cfg_path in config_paths:
        try:
            cfg = Config.load(str(cfg_path))
        except FileNotFoundError:
            print(f"Config not found: {cfg_path}", file=sys.stderr)
            overall = 2
            continue
        except Exception as e:
            print(f"Failed to parse {cfg_path}: {e}", file=sys.stderr)
            overall = 2
            continue

        log = setup_logger(cfg.log)
        if multi:
            log.info("###### Model: %s (%s) ######", cfg.name, cfg_path.name)

        if args.command == "run":
            rc = cmd_run(cfg, log, args.dry_run)
        elif args.command == "list":
            rc = cmd_list(cfg, log)
        elif args.command == "verify":
            rc = cmd_verify(cfg, log)
        else:
            rc = 2

        if rc != 0:
            overall = rc if overall == 0 else overall
            if multi:
                log.error("###### Model %s FAILED (rc=%d) ######", cfg.name, rc)

    return overall


if __name__ == "__main__":
    sys.exit(main())
