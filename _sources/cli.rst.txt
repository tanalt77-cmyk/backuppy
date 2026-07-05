Command line
============

The single entry point is ``backuppy``. Global options:

``--version``
    Print the engine version and exit.

``--no-progress``
    Suppress the live progress lines (percentage counters) in the log — useful
    for non-interactive cron output.

Models are referenced by **name** (the stem of a file in the configs directory,
default ``/etc/backuppy``) or by an explicit path to a ``.yml`` file.

.. _cli:run:

``run`` — run one or more models
--------------------------------

::

    backuppy run [NAMES...] [--all] [--config FILE] [--configs-dir DIR]
                 [--dry-run] [--no-lock] [--lock-timeout N] [--lock-file PATH]

Executes the full pipeline for each named model. Key options:

``--dry-run``
    Resolve and validate everything and log what *would* happen, but perform no
    dumps, uploads or deletions.

``--all``
    Run every model found in the configs directory.

``--no-lock``
    Do not take the host-wide lock (advanced; normally you want the lock).

``--lock-file PATH``
    Lock file location. Default ``/run/lock/backuppy.lock``.

``--lock-timeout N``
    Seconds to wait for a competing run to finish before giving up. Default
    ``21600`` (6 hours). Only one ``backuppy run`` proceeds on a host at a time;
    others print ``another backup is running on this host — waiting…`` and block
    up to this timeout.

**Exit codes:** ``0`` success (possibly with warnings), ``1`` the run failed.
A run that produces **no artifacts** — for example every source was skipped, or
staging ran out of disk — is treated as a **failure** (exit ``1`` and a failure
notification), never a silent success. See :doc:`notifications`.

``verify`` — check without moving data
--------------------------------------

::

    backuppy verify [NAMES...] [--all]

Loads the configuration, checks that each enabled destination is reachable and
writable, and reports problems. Does not dump databases or upload anything.

``list`` / ``models`` — show configured models
----------------------------------------------

::

    backuppy list
    backuppy models

List the models discovered in the configs directory.

``usage`` — storage usage
-------------------------

::

    backuppy usage [--json]

Report how much space each destination is using. ``--json`` emits
machine-readable output (used by the portal's *Storage* view).

``prune`` — inspect and trim retained copies
--------------------------------------------

::

    backuppy prune [--json] [--dest TYPE] [--delete RUNDIR] [--yes]

Without ``--delete`` it lists the retained runs/files per destination.

``--delete RUNDIR``
    Remove a specific run directory (or file) …
``--dest TYPE``
    … from a specific destination (``local``, ``s3``, ``webdav``, …).
``--yes`` / ``-y``
    Do not prompt for confirmation.

``new`` — scaffold a model from a template
------------------------------------------

::

    backuppy new [NAME] [--template NAME] [--dir DIR] [--force] [--list]

Creates ``<dir>/<name>.yml`` from a built-in template. ``--list`` shows the
available templates; ``--force`` overwrites an existing file.

``notify`` — manage/test notifications
--------------------------------------

::

    backuppy notify add  MODEL --channel {email,telegram}
    backuppy notify test MODEL --channel {email,telegram}

``add`` interactively appends a notification channel to a model; ``test`` sends
a test message through the configured channel so you can confirm delivery.

``migrate`` — upgrade configuration files
-----------------------------------------

::

    backuppy migrate [TARGETS...] [--all] [--dry-run] [--yes]

Rewrites older model files to the current schema. ``--dry-run`` shows the
changes without writing; ``--all`` migrates every model.
