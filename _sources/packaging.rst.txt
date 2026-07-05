Packaging
=========

Between collecting artifacts and uploading them, backuppy can compress, encrypt
and split each archive. All three are optional and applied in that order.

Compression
-----------

.. code-block:: yaml

    compression:
      method: zstd        # gzip | bzip2 | xz | zstd | none
      level: null         # method-specific level; null = the tool's default

``zstd`` gives the best speed/ratio trade-off and is recommended. Use ``none``
to upload an already-compressed artifact unchanged (for example a *promote*
model re-uploading a finished ``.tar.zst`` — see :doc:`guides/promote`).

Progress is logged every 10% while archiving and compressing large files.

Encryption
----------

.. code-block:: yaml

    encryption:
      enabled: false
      method: gpg-symmetric      # gpg-symmetric | gpg-asymmetric | openssl
      # gpg-symmetric / openssl (passphrase-based):
      passphrase_file: /etc/backuppy/secret.pass
      openssl_pass_file: /etc/backuppy/openssl.pass
      # gpg-asymmetric (public-key):
      recipient: ops@example.com
      gpg_home: /root/.gnupg

- **gpg-symmetric** — one passphrase from ``passphrase_file``.
- **gpg-asymmetric** — encrypt to a public key ``recipient`` (no shared secret
  on the agent).
- **openssl** — passphrase from ``openssl_pass_file``.

Keep the passphrase/keys off the backup destinations — otherwise the encryption
protects nothing.

Splitting
---------

Break large archives into fixed-size parts so they fit destination limits or
resume more easily.

.. code-block:: yaml

    splitter:
      enabled: false
      chunk_size_mb: 1024     # size of each part
      only_above_mb: 0        # only split archives larger than this (0 = always)

Parts are uploaded and rotated together as a set.
