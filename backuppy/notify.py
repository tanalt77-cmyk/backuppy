"""Notification channels: Email (SMTP) and Telegram (Bot API)."""
from __future__ import annotations

import logging
import smtplib
import ssl
from email.message import EmailMessage

import requests

from .config import EmailCfg, TelegramCfg


def send_email(cfg: EmailCfg, subject: str, body: str,
               log: logging.Logger) -> None:
    if not cfg.enabled:
        return
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg.from_addr
    msg["To"] = ", ".join(cfg.to_addrs)
    msg.set_content(body)

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

    # Telegram message limit is 4096 chars; trim body if needed
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
               trigger: str, subject: str, body: str,
               log: logging.Logger) -> None:
    """Dispatch to all configured channels whose 'when' matches trigger."""
    for ch_cfg, sender in [(email_cfg, send_email), (telegram_cfg, send_telegram)]:
        if not ch_cfg.enabled:
            continue
        when = getattr(ch_cfg, "when", "on_failure")
        if when == "always" or when == trigger:
            sender(ch_cfg, subject, body, log)
