Quick start
===========

A *model* is a single YAML file. This walk-through builds one from scratch and
runs it.

1. Create a model
-----------------

Scaffold a model from a template::

    backuppy new myapp --template files

That writes ``/etc/backuppy/myapp.yml``. List the available templates with
``backuppy new --list``.

2. Describe what to back up
---------------------------

Edit the file. The smallest useful model collects some folders and keeps a
local copy plus an off-site copy on S3:

.. code-block:: yaml

    name: myapp

    sources:
      - type: files
        paths:
          - /srv/myapp/data
          - /srv/myapp/config
        excludes:
          - "*/cache/*"
          - "*.tmp"

    compression:
      method: zstd

    local:
      enabled: true
      path: /var/backups/backuppy
      keep_last: 7

    s3:
      enabled: true
      bucket: my-offsite-bucket
      region: eu-central-1
      prefix: myapp
      access_key_id: "AKIA..."
      secret_access_key: "..."
      keep_last: 30

    email:
      enabled: true
      when: on_failure
      smtp_host: smtp.example.com
      from_addr: backups@example.com
      to_addrs: [ops@example.com]

3. Check it
-----------

Validate configuration and destination reachability without moving data::

    backuppy verify myapp

See what a run would do, without doing it::

    backuppy run myapp --dry-run

4. Run it
---------

::

    backuppy run myapp

A run performs, in order: **triggers** (database dumps) → **sources** (file
collection) → **compress / encrypt / split** → **upload with verification** to
every enabled destination → **rotation** → **notification**. Progress and the
final ``=== Done in Ns ===`` line are written to the log
(``/var/log/backuppy-myapp.log``) and to the terminal.

5. Schedule it
--------------

Add a ``cron`` entry (the portal manages these for you inside
``# BEGIN/END backuppy-portal:<model>`` markers)::

    # daily at 02:00
    0 2 * * * /usr/local/bin/backuppy run myapp >> /var/log/backuppy-myapp.log 2>&1

.. tip::

   Only one ``backuppy run`` executes on a host at a time — the engine takes a
   lock and later runs **wait** for it (see :ref:`cli:run`). This keeps two
   large backups from competing for disk and bandwidth.

Where to next
-------------

- Back up a SQL Server database: :doc:`guides/mssql`.
- Add weekly/monthly copies that re-upload the newest daily archive without
  re-packing it: :doc:`guides/promote`.
- The full list of every YAML key: :doc:`configuration`.
