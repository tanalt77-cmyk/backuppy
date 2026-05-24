"""Encryption methods: GPG (symmetric/asymmetric) and OpenSSL AES-256."""
from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

from .config import EncryptionCfg


def encrypt_file(path: Path, cfg: EncryptionCfg, log: logging.Logger) -> Path:
    if not cfg.enabled:
        return path

    method = cfg.method.lower().replace("_", "-")
    if method == "gpg-symmetric":
        return _gpg_symmetric(path, cfg, log)
    if method == "gpg-asymmetric":
        return _gpg_asymmetric(path, cfg, log)
    if method == "openssl":
        return _openssl(path, cfg, log)
    raise ValueError(f"Unknown encryption method: {cfg.method}")


def _gpg_symmetric(path: Path, cfg: EncryptionCfg, log: logging.Logger) -> Path:
    if not cfg.passphrase_file:
        raise RuntimeError("encryption.passphrase_file is required for gpg-symmetric")
    pf = Path(cfg.passphrase_file)
    if not pf.exists():
        raise FileNotFoundError(f"passphrase file not found: {pf}")

    out = path.with_suffix(path.suffix + ".gpg")
    log.info("Encrypting (GPG symmetric) → %s", out.name)

    cmd = [
        "gpg", "--batch", "--yes",
        "--passphrase-file", str(pf),
        "--symmetric", "--cipher-algo", "AES256",
        "--output", str(out),
        str(path),
    ]
    env = _gpg_env(cfg)
    res = subprocess.run(cmd, capture_output=True, text=True, env=env)
    if res.returncode != 0:
        raise RuntimeError(f"gpg failed: {res.stderr.strip()}")

    path.unlink()
    return out


def _gpg_asymmetric(path: Path, cfg: EncryptionCfg, log: logging.Logger) -> Path:
    """Encrypts with recipient's public key. The user holding the private key
    can decrypt. Useful when the backup machine should never know decryption
    secrets — only encryption is possible there.
    """
    if not cfg.recipient:
        raise RuntimeError("encryption.recipient is required for gpg-asymmetric")

    out = path.with_suffix(path.suffix + ".gpg")
    log.info("Encrypting (GPG asymmetric, recipient=%s) → %s",
             cfg.recipient, out.name)

    cmd = [
        "gpg", "--batch", "--yes",
        "--trust-model", "always",  # don't prompt if the key isn't fully trusted
        "--recipient", cfg.recipient,
        "--encrypt",
        "--output", str(out),
        str(path),
    ]
    env = _gpg_env(cfg)
    res = subprocess.run(cmd, capture_output=True, text=True, env=env)
    if res.returncode != 0:
        raise RuntimeError(
            f"gpg failed: {res.stderr.strip()}\n"
            f"Hint: make sure public key for {cfg.recipient} is imported "
            f"(gpg --import recipient.pub)"
        )

    path.unlink()
    return out


def _openssl(path: Path, cfg: EncryptionCfg, log: logging.Logger) -> Path:
    """OpenSSL AES-256-CBC with PBKDF2 key derivation."""
    if not cfg.openssl_pass_file:
        raise RuntimeError("encryption.openssl_pass_file is required for openssl")
    pf = Path(cfg.openssl_pass_file)
    if not pf.exists():
        raise FileNotFoundError(f"openssl pass file not found: {pf}")

    out = path.with_suffix(path.suffix + ".enc")
    log.info("Encrypting (OpenSSL AES-256-CBC) → %s", out.name)

    cmd = [
        "openssl", "enc", "-aes-256-cbc",
        "-pbkdf2", "-iter", "100000",  # modern key derivation
        "-salt",
        "-pass", f"file:{pf}",
        "-in", str(path),
        "-out", str(out),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"openssl failed: {res.stderr.strip()}")

    path.unlink()
    return out


def _gpg_env(cfg: EncryptionCfg) -> dict[str, str]:
    env = dict(os.environ)
    if cfg.gpg_home:
        env["GNUPGHOME"] = cfg.gpg_home
    return env
