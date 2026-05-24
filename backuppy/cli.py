"""Command-line interface."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .config import Config
from .core import setup_logger, cmd_run, cmd_list, cmd_verify, cmd_models


def _collect_configs(configs: list[str] | None,
                     configs_dir: str | None) -> list[Path]:
    """Resolve --config (repeatable) and --configs-dir to a list of files."""
    paths: list[Path] = []

    if configs:
        for c in configs:
            p = Path(c).expanduser()
            if not p.exists():
                print(f"Config not found: {p}", file=sys.stderr)
                sys.exit(2)
            paths.append(p)

    if configs_dir:
        d = Path(configs_dir).expanduser()
        if not d.is_dir():
            print(f"Not a directory: {d}", file=sys.stderr)
            sys.exit(2)
        for p in sorted(d.iterdir()):
            if p.is_file() and p.suffix in (".yml", ".yaml") and \
               not p.name.startswith(("_", ".", "config.example")):
                # Heuristic: skip files in shared/ subdirs and shared-named files
                paths.append(p)

    if not paths:
        print("No config file(s) given. Use --config / -c or --configs-dir.",
              file=sys.stderr)
        sys.exit(2)

    return paths


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="backuppy",
        description="Modular backup tool inspired by Backup gem."
    )
    ap.add_argument("--version", action="version",
                    version=f"backuppy {__version__}")
    ap.add_argument("command", choices=["run", "list", "verify", "models"])
    ap.add_argument("--config", "-c", action="append",
                    help="Path to a config YAML (repeatable for multi-run).")
    ap.add_argument("--configs-dir", "-d",
                    help="Directory of *.yml/*.yaml configs to run/list.")
    ap.add_argument("--dry-run", action="store_true",
                    help="Show what would be done without doing it (run only).")
    args = ap.parse_args()

    config_paths = _collect_configs(args.config, args.configs_dir)

    if args.command == "models":
        return cmd_models(config_paths)

    # For multi-config: load each, set up its own logger, run sequentially.
    # Return non-zero if ANY of them failed.
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
