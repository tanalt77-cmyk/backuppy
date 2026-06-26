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
from .core import (
    setup_logger, cmd_run, cmd_list, cmd_verify, cmd_models,
    model_usage, model_storage_detail, delete_run_dir,
)
from .cmd_new import cmd_new, cmd_list_templates, TEMPLATES


DEFAULT_CONFIGS_DIR = "/etc/backuppy"


def _acquire_run_lock(path: str, timeout: int):
    """Acquire a host-global exclusive lock so only one backup runs at a time.

    Returns the open lock file (keep it referenced to hold the lock; closing it
    releases). Waits up to `timeout` seconds for a competing run to finish,
    logging a one-time notice, then raises TimeoutError if it never frees up.
    """
    import fcntl
    import os
    import time

    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd = open(path, "w")
    except OSError:
        path = "/tmp/backuppy.lock"
        fd = open(path, "w")

    start = time.monotonic()
    notified = False
    while True:
        try:
            fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            if notified:
                print("[backuppy] previous run finished — starting now.", file=sys.stderr)
            return fd
        except OSError:
            if time.monotonic() - start >= timeout:
                fd.close()
                raise TimeoutError(
                    f"another backuppy run is still in progress; waited "
                    f"{timeout}s for the lock ({path}) and gave up — skipping this run")
            if not notified:
                print(f"[backuppy] another backup is running on this host — waiting for "
                      f"it to finish (lock {path}, up to {timeout}s)...", file=sys.stderr)
                notified = True
            time.sleep(5)


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


def _fmt_bytes(b: int) -> str:
    f = float(b)
    for u in ("B", "KB", "MB", "GB", "TB"):
        if f < 1024 or u == "TB":
            return f"{f:.1f} {u}"
        f /= 1024
    return f"{f:.1f} TB"


def cmd_usage(config_paths, as_json: bool) -> int:
    """Report each model's backup footprint per destination."""
    import json as _json
    import logging

    quiet = logging.getLogger("backuppy.usage")
    if not quiet.handlers:
        quiet.addHandler(logging.NullHandler())
    quiet.propagate = False

    results = []
    for cfg_path in config_paths:
        try:
            cfg = Config.load(str(cfg_path))
        except Exception as e:  # noqa: BLE001
            if not as_json:
                print(f"skip {cfg_path}: {e}", file=sys.stderr)
            continue
        results.append(model_usage(cfg, quiet))

    if as_json:
        print(_json.dumps({"models": results}))
        return 0

    grand = 0
    for m in results:
        print(f"{m['name']}  —  {_fmt_bytes(m['total_bytes'])}")
        for d in m["destinations"]:
            if "error" in d:
                print(f"    {d['type']:<7} {d['location']}  ERROR: {d['error']}")
            else:
                print(f"    {d['type']:<7} {_fmt_bytes(d['bytes']):>10}  "
                      f"({d.get('backups', 0)} backups, {d['files']} files)  {d['location']}")
        grand += m["total_bytes"]
    if len(results) > 1:
        print(f"\nTotal: {_fmt_bytes(grand)}")
    return 0


def cmd_prune(config_paths, args) -> int:
    """List run-dirs per destination, or delete one chosen run-dir."""
    import json as _json
    import logging

    quiet = logging.getLogger("backuppy.prune")
    if not quiet.handlers:
        quiet.addHandler(logging.NullHandler())
    quiet.propagate = False

    # ---- delete mode ----
    if args.delete:
        if len(config_paths) != 1:
            print("prune --delete needs exactly one model (name it explicitly)",
                  file=sys.stderr)
            return 2
        try:
            cfg = Config.load(str(config_paths[0]))
        except Exception as e:  # noqa: BLE001
            print(f"Failed to load {config_paths[0]}: {e}", file=sys.stderr)
            return 2
        try:
            res = delete_run_dir(cfg, args.delete, args.dest, quiet,
                                 dry_run=not args.yes)
        except ValueError as e:
            print(str(e), file=sys.stderr)
            return 2
        if args.json:
            print(_json.dumps(res))
            return 0
        verb = "DELETED" if not res["dry_run"] else "would delete"
        for r in res["results"]:
            if "deleted" in r or "would_delete" in r:
                print(f"  {verb} {res['name']}/{args.delete} on {r['type']} "
                      f"({_fmt_bytes(r.get('bytes', 0))})")
            elif "skipped" in r:
                print(f"  {r['type']}: {r['skipped']}")
            elif "error" in r:
                print(f"  {r['type']}: ERROR {r['error']}")
        if res["dry_run"]:
            print("  (dry-run — pass --yes to actually delete)")
        return 0

    # ---- list mode ----
    results = []
    for cfg_path in config_paths:
        try:
            cfg = Config.load(str(cfg_path))
        except Exception as e:  # noqa: BLE001
            if not args.json:
                print(f"skip {cfg_path}: {e}", file=sys.stderr)
            continue
        results.append(model_storage_detail(cfg, quiet))

    if args.json:
        print(_json.dumps({"models": results}))
        return 0

    for m in results:
        print(f"{m['name']}")
        for d in m["destinations"]:
            if "error" in d:
                print(f"  {d['type']}: ERROR {d['error']}")
                continue
            print(f"  {d['type']}  ({d['location']})")
            for r in d["runs"]:
                flag = "" if r["is_run"] else "  [not a run-dir]"
                print(f"      {r['name']}  {_fmt_bytes(r['bytes']):>10}  "
                      f"({r['files']} files){flag}")
            if d.get("loose_files"):
                print(f"      (loose files: {d['loose_files']}, "
                      f"{_fmt_bytes(d['loose_bytes'])})")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="backuppy",
        description="Modular backup tool inspired by Backup gem."
    )
    ap.add_argument("--version", action="version",
                    version=f"backuppy {__version__}")
    ap.add_argument("--no-progress", action="store_true",
                    help="Disable progress bars and progress log lines "
                         "(useful in cron).")

    sub = ap.add_subparsers(dest="command", required=True,
                            metavar="COMMAND")

    # ---- run ----
    p_run = sub.add_parser("run", help="Run one or more backup models")
    _add_target_args(p_run)
    p_run.add_argument("--dry-run", action="store_true",
                       help="Show what would be done without doing it.")
    p_run.add_argument("--no-lock", action="store_true",
                       help="Don't serialize with other backuppy runs on this host.")
    p_run.add_argument("--lock-timeout", type=int, default=21600,
                       help="Seconds to wait for another run to finish before "
                            "giving up (default 21600 = 6h).")
    p_run.add_argument("--lock-file", default="/run/lock/backuppy.lock",
                       help="Host-global lock file used to serialize runs.")

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

    # ---- usage ----
    p_usage = sub.add_parser("usage",
                             help="Report how much space each model's backups take")
    _add_target_args(p_usage)
    p_usage.add_argument("--json", action="store_true",
                         help="Emit machine-readable JSON instead of a table")

    # ---- prune ----
    p_prune = sub.add_parser("prune",
                             help="List or delete individual backup run-dirs on the storages")
    _add_target_args(p_prune)
    p_prune.add_argument("--json", action="store_true",
                         help="Emit machine-readable JSON instead of a table")
    p_prune.add_argument("--delete", metavar="RUNDIR",
                         help="Delete this run-dir (YYYYMMDD-HHMMSS) from the model's storages")
    p_prune.add_argument("--dest", metavar="TYPE",
                         help="Limit --delete to one destination (s3/webdav/local/...)")
    p_prune.add_argument("--yes", "-y", action="store_true",
                         help="Actually delete (without this, --delete is a dry-run)")

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

    # ---- notify ----
    p_notify = sub.add_parser("notify",
                              help="Add or test notification channels")
    notify_sub = p_notify.add_subparsers(dest="notify_action",
                                         metavar="ACTION", required=True)
    p_notify_add = notify_sub.add_parser(
        "add", help="Interactive wizard to add an email/telegram block")
    p_notify_add.add_argument("model", help="Model name or path to YAML")
    p_notify_add.add_argument("--channel",
                              choices=["email", "telegram"],
                              help="Skip the channel prompt")

    p_notify_test = notify_sub.add_parser(
        "test", help="Send a one-shot test notification via configured channels")
    p_notify_test.add_argument("model", help="Model name or path to YAML")
    p_notify_test.add_argument("--channel",
                               choices=["email", "telegram"],
                               help="Only test this specific channel")

    # ---- migrate ----
    p_migrate = sub.add_parser(
        "migrate",
        help="Auto-update *_path fields for v3.10.0 model-name prefix change")
    p_migrate.add_argument("targets", nargs="*",
                           help="Model names or paths to migrate")
    p_migrate.add_argument("--all", action="store_true", dest="all_flag",
                           help=f"Migrate every model in {DEFAULT_CONFIGS_DIR}/")
    p_migrate.add_argument("--dry-run", action="store_true",
                           help="Show what would change without writing")
    p_migrate.add_argument("--yes", "-y", action="store_true",
                           help="Skip confirmation prompts")

    args = ap.parse_args()

    # Apply global --no-progress flag
    if args.no_progress:
        from . import progress
        progress.disable()

    if args.command == "new":
        if args.list:
            return cmd_list_templates()
        if not args.name:
            print("Error: 'name' is required (or use --list to see templates).",
                  file=sys.stderr)
            return 2
        return cmd_new(args.name, args.template, Path(args.dir).expanduser(),
                       args.force)

    if args.command == "notify":
        from .cmd_notify import cmd_notify_add, cmd_notify_test
        if args.notify_action == "add":
            return cmd_notify_add(args.model, args.channel)
        if args.notify_action == "test":
            return cmd_notify_test(args.model, args.channel)
        return 2

    if args.command == "migrate":
        from .cmd_migrate import cmd_migrate
        return cmd_migrate(args.targets, args.dry_run, args.all_flag, args.yes)

    # For models/usage without any target → operate on DEFAULT_CONFIGS_DIR
    if args.command in ("models", "usage") and not (args.names or args.config or
                                          args.configs_dir or args.all):
        args.all = True
    # prune in list mode (no --delete) also defaults to all models
    if args.command == "prune" and not getattr(args, "delete", None) and not (
            args.names or args.config or args.configs_dir or args.all):
        args.all = True

    config_paths = _collect_configs(
        getattr(args, "names", []) or [],
        getattr(args, "config", None),
        getattr(args, "configs_dir", None),
        getattr(args, "all", False),
    )

    if args.command == "models":
        return cmd_models(config_paths)

    if args.command == "usage":
        return cmd_usage(config_paths, getattr(args, "json", False))

    if args.command == "prune":
        return cmd_prune(config_paths, args)

    # Serialize `run` invocations on this host: while one backup runs, another
    # (e.g. a weekly/monthly model fired by cron) waits for it to finish, so a
    # single agent never runs two heavy backups at once. Read-only commands
    # (list/verify) and --dry-run are never locked.
    run_lock = None
    if (args.command == "run" and not getattr(args, "no_lock", False)
            and not getattr(args, "dry_run", False)):
        try:
            run_lock = _acquire_run_lock(args.lock_file, args.lock_timeout)
        except TimeoutError as e:
            print(f"[backuppy] {e}", file=sys.stderr)
            return 1

    overall = 0
    multi = len(config_paths) > 1
    try:
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
    finally:
        if run_lock is not None:
            run_lock.close()

    return overall


if __name__ == "__main__":
    sys.exit(main())
