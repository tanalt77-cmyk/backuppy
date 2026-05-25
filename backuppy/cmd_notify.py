"""`backuppy notify` — interactive wizard for adding/testing notification channels.

Subcommands:
  add <model>   Interactive wizard to add or replace an email/telegram block
                in the model's YAML.
  test <model>  Send a one-shot test message via all enabled channels.
"""
from __future__ import annotations

import getpass
import logging
import socket
import sys
from pathlib import Path
from typing import Any

from .config import Config, EmailCfg, TelegramCfg


def _input(prompt: str, default: str = "", required: bool = False,
           hidden: bool = False, validator=None) -> str:
    """Prompt with default-value support and optional validation."""
    suffix = f" [{default}]" if default else ""
    while True:
        if hidden:
            val = getpass.getpass(f"{prompt}{suffix}: ").strip()
        else:
            val = input(f"{prompt}{suffix}: ").strip()
        if not val and default:
            val = default
        if not val and required:
            print("  (required — please enter a value)", file=sys.stderr)
            continue
        if validator is not None:
            err = validator(val)
            if err:
                print(f"  ({err})", file=sys.stderr)
                continue
        return val


def _yesno(prompt: str, default: bool = True) -> bool:
    """Yes/No prompt with default."""
    suffix = " (Y/n)" if default else " (y/N)"
    while True:
        val = input(f"{prompt}{suffix}: ").strip().lower()
        if not val:
            return default
        if val in ("y", "yes"):
            return True
        if val in ("n", "no"):
            return False


def _select(prompt: str, options: list[str], default: str | None = None) -> str:
    """Pick one of several options."""
    opts_str = "/".join(options)
    while True:
        suffix = f" [{default}]" if default else ""
        val = input(f"{prompt} ({opts_str}){suffix}: ").strip().lower()
        if not val and default:
            return default
        if val in options:
            return val
        print(f"  (must be one of: {opts_str})", file=sys.stderr)


def _resolve_model_path(model_name_or_path: str) -> Path:
    """Either a path with .yml/.yaml suffix, or a model name in /etc/backuppy."""
    p = Path(model_name_or_path)
    if p.suffix in (".yml", ".yaml") and p.exists():
        return p
    # Try /etc/backuppy/<name>.yml
    from .cli import DEFAULT_CONFIGS_DIR
    base = Path(DEFAULT_CONFIGS_DIR)
    for ext in (".yml", ".yaml"):
        candidate = base / f"{model_name_or_path}{ext}"
        if candidate.exists():
            return candidate
    raise FileNotFoundError(
        f"Model '{model_name_or_path}' not found in {DEFAULT_CONFIGS_DIR}/"
    )


def _load_yaml_with_comments(path: Path):
    """Load YAML preserving comments and formatting (uses ruamel.yaml)."""
    try:
        from ruamel.yaml import YAML
    except ImportError:
        raise RuntimeError(
            "ruamel.yaml not installed. Run: pip install ruamel.yaml"
        )
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.indent(mapping=2, sequence=4, offset=2)
    with open(path, "r", encoding="utf-8") as f:
        return yaml, yaml.load(f)


def _save_yaml(yaml, data, path: Path) -> None:
    """Write YAML back, preserving comments."""
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f)
    # Match permissions of original (in case it's chmod 600 for credentials)
    try:
        import stat
        st = path.stat()
        path.chmod(st.st_mode & 0o777)
    except Exception:
        pass


# ============================================================================
# Email wizard
# ============================================================================

WHEN_OPTIONS = ["always", "on_failure", "on_warning", "on_success",
                "on_issue", "never"]


def _wizard_email() -> dict:
    """Interactive prompts for email channel. Returns dict to merge into YAML."""
    print("\n--- Configuring email notifications ---\n")

    smtp_host = _input("SMTP host", required=True,
                       validator=lambda v: None if "." in v else "must be a hostname")
    smtp_port = int(_input("SMTP port", default="587",
                           validator=lambda v: None if v.isdigit() and 1 <= int(v) <= 65535
                                                  else "must be a port number"))
    smtp_user = _input("SMTP username", required=True)
    smtp_password = _input("SMTP password", required=True, hidden=True)
    use_tls = _yesno("Use STARTTLS? (yes for port 587, no for port 465)", default=True)
    from_addr = _input("From address", default=smtp_user,
                       validator=lambda v: None if "@" in v else "must be an email")
    to_raw = _input("To addresses (comma-separated)", required=True,
                    validator=lambda v: None if all("@" in t for t in v.split(",")
                                                       if t.strip())
                                          else "must be email addresses")
    to_addrs = [a.strip() for a in to_raw.split(",") if a.strip()]

    when = _select("When to send", WHEN_OPTIONS, default="on_failure")

    return {
        "enabled": True,
        "when": when,
        "smtp_host": smtp_host,
        "smtp_port": smtp_port,
        "smtp_user": smtp_user,
        "smtp_password": smtp_password,
        "use_tls": use_tls,
        "from_addr": from_addr,
        "to_addrs": to_addrs,
    }


def _wizard_telegram() -> dict:
    """Interactive prompts for Telegram channel."""
    print("\n--- Configuring Telegram notifications ---\n")
    print("Need a bot? Open @BotFather in Telegram, /newbot, get the token.")
    print("Need chat_id? Add the bot to a group, send a message, then visit")
    print("  https://api.telegram.org/bot<TOKEN>/getUpdates\n")

    bot_token = _input("Bot token (from @BotFather)", required=True, hidden=True,
                       validator=lambda v: None if ":" in v else "must look like '123456:ABC-DEF...'")
    chat_id = _input("Chat ID (channel or group)", required=True)
    when = _select("When to send", WHEN_OPTIONS, default="on_failure")

    return {
        "enabled": True,
        "when": when,
        "bot_token": bot_token,
        "chat_id": chat_id,
    }


def cmd_notify_add(model: str, channel: str | None = None) -> int:
    """Interactive: add an email or telegram block to the model's YAML."""
    try:
        path = _resolve_model_path(model)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2

    if channel is None:
        channel = _select("Which channel", ["email", "telegram"])

    if channel == "email":
        new_block = _wizard_email()
    elif channel == "telegram":
        new_block = _wizard_telegram()
    else:
        print(f"Unknown channel: {channel}", file=sys.stderr)
        return 2

    # Load YAML with comment preservation
    try:
        yaml, data = _load_yaml_with_comments(path)
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    if data is None:
        data = {}

    if channel in data:
        if not _yesno(f"\n'{channel}:' block already exists in {path.name}. "
                       f"Overwrite?", default=False):
            print("Cancelled.")
            return 0

    data[channel] = new_block
    _save_yaml(yaml, data, path)
    print(f"\n✓ {channel.title()} block written to {path}")

    # Offer test
    if _yesno("\nSend a test notification now?", default=True):
        return cmd_notify_test(model, only_channel=channel)
    return 0


# ============================================================================
# Test command
# ============================================================================

def cmd_notify_test(model: str, only_channel: str | None = None) -> int:
    """Send a one-shot test message via configured channels."""
    try:
        path = _resolve_model_path(model)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2

    try:
        cfg = Config.load(str(path))
    except Exception as e:
        print(f"Failed to parse {path}: {e}", file=sys.stderr)
        return 2

    log = logging.getLogger("backuppy.notify")
    log.setLevel(logging.INFO)
    if not log.handlers:
        h = logging.StreamHandler(sys.stdout)
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s",
                                          "%H:%M:%S"))
        log.addHandler(h)

    from .notify import send_email, send_telegram

    host = socket.gethostname()
    subject = f"[backuppy test] {cfg.name} @ {host}"
    body = (
        f"This is a test message from backuppy.\n\n"
        f"Model: {cfg.name}\n"
        f"Host:  {host}\n"
        f"Path:  {path}\n\n"
        f"If you received this, your notification channel is configured correctly."
    )

    sent_any = False

    if cfg.email.enabled and (only_channel is None or only_channel == "email"):
        print(f"\n→ Sending test email to {', '.join(cfg.email.to_addrs)} "
              f"via {cfg.email.smtp_host}:{cfg.email.smtp_port} ...")
        send_email(cfg.email, subject, body, log)
        sent_any = True

    if cfg.telegram.enabled and (only_channel is None or only_channel == "telegram"):
        print(f"\n→ Sending test Telegram message to chat_id={cfg.telegram.chat_id} ...")
        send_telegram(cfg.telegram, subject, body, log)
        sent_any = True

    if not sent_any:
        if only_channel:
            print(f"\nError: '{only_channel}' is not enabled in {path.name}",
                  file=sys.stderr)
        else:
            print(f"\nNo notification channels enabled in {path.name}.\n"
                  f"Run: backuppy notify add {model}",
                  file=sys.stderr)
        return 1
    return 0
