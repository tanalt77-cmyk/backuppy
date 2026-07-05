Welcome to backuppy's documentation!
=====================================

**backuppy** is an agent-side backup engine for Linux hosts. A single YAML file
describes a *model* — what to back up, how to package it, and where to send it —
and ``backuppy run <model>`` executes it end to end: dump databases, collect
files, compress, (optionally) encrypt and split, upload to one or more
destinations with verification, rotate old copies, and notify you of the
outcome.

It is designed to run unattended from ``cron`` and to be driven at scale by a
control plane (the backuppy portal), but every feature is available on its own
from the command line.

.. rubric:: Highlights

- **Database triggers** — MSSQL, PostgreSQL, MySQL/MariaDB, MongoDB, Redis and
  SQLite dumps, plus a generic command *hook* trigger.
- **File sources** — glob-based file/folder collection with excludes; can
  *promote* a ready archive without re-packing.
- **Many destinations** — a local copy plus WebDAV, Amazon S3 (and
  S3-compatible), SFTP, Dropbox, Google Cloud Storage and Azure Blob — any
  combination at once.
- **Packaging** — gzip / bzip2 / xz / zstd (or none), optional GPG/OpenSSL
  encryption, and size-based splitting of large archives.
- **Retention** — per-destination ``keep_last`` rotation, with an optional
  per-run subdirectory layout (``group_by_run``).
- **Integrity** — size or checksum verification after every upload.
- **Notifications** — e-mail and Telegram, sent on failure, on warning, on
  success or always.
- **Safety** — a host-wide run lock, and a hard failure (never a silent
  "success") when a run produces nothing to upload.

.. toctree::
   :maxdepth: 2
   :caption: Getting started

   installation
   quickstart

.. toctree::
   :maxdepth: 2
   :caption: Reference

   cli
   configuration
   triggers
   destinations
   packaging
   retention
   notifications
   hooks

.. toctree::
   :maxdepth: 2
   :caption: Guides

   guides/mssql
   guides/promote
   guides/restore
   scheduling
   guides/troubleshooting

.. toctree::
   :maxdepth: 1
   :caption: Project

   development
   changelog


Indices and tables
==================

* :ref:`genindex`
* :ref:`search`
