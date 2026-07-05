Retention & layout
==================

Each destination keeps the most recent ``keep_last`` copies and deletes older
ones after a successful run. Retention is **per destination**, so you can keep,
say, 7 local copies but 30 on S3.

Two layouts
-----------

**Per-file (default, ``group_by_run: false``).** Artifacts are written directly
into the destination with their own names; rotation groups them by filename
prefix and keeps the newest ``keep_last`` of each.

**Per-run (``group_by_run: true``).** Every run creates a ``YYYYMMDD-HHMMSS``
subdirectory and all of that run's artifacts go inside it. Rotation keeps the
newest ``keep_last`` *run directories* — cleaner when a single run produces many
files (e.g. several databases plus files).

.. code-block:: yaml

    group_by_run: true

    local:  { enabled: true, path: /var/backups/backuppy, keep_last: 7 }
    s3:     { enabled: true, bucket: my-bucket, keep_last: 30 }

How rotation runs
-----------------

Rotation prunes to ``keep_last`` after each successful run. When a destination
holds a backlog well above the limit (for example after changing the naming
scheme, or a burst of manual runs), it is trimmed **gradually — one oldest copy
per run** rather than all at once; this is deliberately conservative. Over a few
scheduled runs the destination converges to exactly ``keep_last`` copies.

Inspecting and pruning by hand
------------------------------

::

    backuppy prune myapp                    # list retained runs per destination
    backuppy prune myapp --dest s3 --delete 20260626-020001 --yes

See :doc:`cli` for the full ``prune`` and ``usage`` options.
