Scheduling & operations
=======================

backuppy does not schedule itself — drive it from ``cron`` (or systemd timers).
When agents are managed by the portal, these cron entries are written for you
inside ``# BEGIN/END backuppy-portal:<model>`` markers; the examples below are
for hand-managed hosts.

Cron with a lock
----------------

.. code-block:: text

    PATH=/usr/local/bin:/usr/bin:/bin
    MAILTO=""

    # Daily FULL at 02:17
    17 2 * * *  /usr/bin/flock -n /var/lock/backuppy-app.lock \
                /usr/local/bin/backuppy --no-progress run app \
                >> /var/log/backuppy-cron.log 2>&1

    # Hourly LOG at *:30
    30 * * * *  /usr/bin/flock -n /var/lock/backuppy-app.lock \
                /usr/local/bin/backuppy --no-progress run app-log \
                >> /var/log/backuppy-cron.log 2>&1

    # Weekly (Mon 03:17) and monthly (1st 04:17) — wait for the lock, don't skip
    17 3 * * 1  /usr/bin/flock /var/lock/backuppy-app.lock \
                /usr/local/bin/backuppy --no-progress run app-weekly \
                >> /var/log/backuppy-cron.log 2>&1
    17 4 1 * *  /usr/bin/flock /var/lock/backuppy-app.lock \
                /usr/local/bin/backuppy --no-progress run app-monthly \
                >> /var/log/backuppy-cron.log 2>&1

``flock -n`` **skips** the run if another still holds the lock (good for
frequent daily/hourly jobs). Drop ``-n`` to **wait** instead — useful for
weekly/monthly, which should still run even if a daily overran. Note backuppy
*also* takes its own host-wide lock (see :ref:`cli:run`), so concurrent runs are
serialised even without ``flock``.

Log rotation
------------

.. code-block:: text

    # /etc/logrotate.d/backuppy
    /var/log/backuppy-cron.log /var/log/backuppy.log {
        weekly
        rotate 4
        compress
        delaycompress
        missingok
        notifempty
        create 0644 root root
    }

Migrating older configs
-----------------------

Since v3.10 backuppy appends ``<model_name>/`` to every destination path
automatically. If an older config already includes the model name in
``remote_path`` (e.g. ``Backups/app``), the path would double up
(``Backups/app/app/``). Fix it in place::

    backuppy migrate --all --dry-run     # preview
    backuppy migrate --all               # apply (with prompts)
    backuppy migrate --all --yes         # apply non-interactively

A ``.bak`` of each file is written before changes so you can roll back.
