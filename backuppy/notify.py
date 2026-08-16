"""Notification channels: Email (SMTP) and Telegram (Bot API).

Notification triggers (the `when:` field) — full set:

  always       — always send (success / warning / failure)
  on_failure   — only when the run raised an exception
  on_warning   — successful run, but at least one WARNING was logged
  on_success   — clean successful run (no warnings)
  on_issue     — warning or failure (anything not perfectly clean)
  never        — never send (handy to disable a channel without removing it)
"""
from __future__ import annotations

import logging
import smtplib
import ssl
from email.message import EmailMessage

import requests

from .config import EmailCfg, TelegramCfg


# Valid values for the `when:` field
VALID_WHEN = {"always", "on_failure", "on_warning", "on_success",
              "on_issue", "never"}


class WarningCollector(logging.Handler):
    """A log Handler that collects WARNING+ messages during a backup run.

    Attach to the backuppy logger; later read .messages and detach when done.
    """

    def __init__(self):
        super().__init__(level=logging.WARNING)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.messages.append(self.format(record))
        except Exception:
            self.handleError(record)


def should_notify(when: str, outcome: str) -> bool:
    """Decide whether a channel with `when` should fire given the run `outcome`.

    outcome ∈ {"success", "warning", "failure"}
    """
    if when == "never":
        return False
    if when == "always":
        return True
    if when == "on_failure":
        return outcome == "failure"
    if when == "on_warning":
        return outcome == "warning"
    if when == "on_success":
        return outcome == "success"
    if when == "on_issue":
        return outcome in ("warning", "failure")
    # Unknown — fail safe (notify so user notices the misconfig)
    return True


def send_email(cfg: EmailCfg, subject: str, body: str,
               log: logging.Logger, log_path: str | None = None) -> None:
    if not cfg.enabled:
        return
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg.from_addr
    msg["To"] = ", ".join(cfg.to_addrs)
    msg.set_content(body)

    # Attach the run log as a .txt file (rather than dumping it in the body),
    # so the email stays readable and the full log is one click away. Best
    # effort: a missing/unreadable log never blocks the notification.
    if log_path:
        try:
            import os
            with open(log_path, "rb") as fh:
                data = fh.read()
            # Cap very large logs so we don't send a huge attachment; keep the
            # tail, which holds the error and the "=== Done ===" marker.
            max_bytes = 512 * 1024
            if len(data) > max_bytes:
                data = b"[... log truncated, showing last %d KB ...]\n\n" % (
                    max_bytes // 1024) + data[-max_bytes:]
            fname = os.path.basename(log_path) or "backuppy.log"
            msg.add_attachment(data, maintype="text", subtype="plain",
                               filename=fname)
        except Exception as e:  # noqa: BLE001 — attachment is optional
            log.debug("could not attach log %s: %s", log_path, e)

    try:
        with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=30) as s:
            s.ehlo()
            if cfg.use_tls:
                s.starttls(context=ssl.create_default_context())
                s.ehlo()
            if cfg.smtp_user:
                s.login(cfg.smtp_user, cfg.smtp_password)
            s.send_message(msg)
        log.info("Email sent (%s)", ", ".join(cfg.to_addrs))
    except Exception as e:
        log.error("Email send failed: %s", e)


def send_telegram(cfg: TelegramCfg, subject: str, body: str,
                  log: logging.Logger) -> None:
    if not cfg.enabled:
        return
    if not cfg.bot_token or not cfg.chat_id:
        log.error("Telegram: bot_token and chat_id required")
        return

    text = f"*{subject}*\n\n```\n{body}\n```"
    if len(text) > 4000:
        text = text[:3990] + "\n[...]```"

    url = f"https://api.telegram.org/bot{cfg.bot_token}/sendMessage"
    try:
        r = requests.post(url, json={
            "chat_id": cfg.chat_id,
            "text": text,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        }, timeout=cfg.timeout)
        if r.status_code != 200:
            log.error("Telegram failed: %s %s", r.status_code, r.text[:200])
        else:
            log.info("Telegram sent (chat_id=%s)", cfg.chat_id)
    except Exception as e:
        log.error("Telegram send failed: %s", e)


def notify_all(email_cfg: EmailCfg, telegram_cfg: TelegramCfg,
               outcome: str, subject: str, body: str,
               log: logging.Logger, log_path: str | None = None) -> None:
    """Dispatch to all configured channels whose `when` matches `outcome`."""
    for ch_cfg, sender in [(email_cfg, send_email),
                           (telegram_cfg, send_telegram)]:
        if not ch_cfg.enabled:
            continue
        when = getattr(ch_cfg, "when", "on_failure")
        if when not in VALID_WHEN:
            log.warning("Notification: unknown when=%r, treating as on_failure", when)
            when = "on_failure"
        if should_notify(when, outcome):
            if sender is send_email:
                sender(ch_cfg, subject, body, log, log_path)
            else:
                sender(ch_cfg, subject, body, log)
