Triggers & sources
==================

A run has two input stages:

**Triggers** produce fresh dumps on disk (database backups). Each trigger writes
into its ``output_dir`` (``output_dir_windows`` for MSSQL) and its job ends once
the files are there.

**Sources** collect files that already exist on disk — including the dumps the
triggers just produced — and hand them to the packaging/upload pipeline.

A typical database model uses an MSSQL trigger to write ``.bak`` files, then a
``files`` source to pick them up.

Sources
-------

files
~~~~~

.. code-block:: yaml

    sources:
      - type: files
        paths:                       # files/folders; glob patterns allowed
          - /srv/app/data
          - /mnt/app-sql/*.bak
        excludes:                    # exclude patterns
          - "*/cache/*"
          - "*.tmp"
        delete_after_pickup: false   # remove originals once copied
        archive_name: ""             # if set, pack all matches into one tar of this name

``paths``
    Files or directories to include. Glob patterns are expanded at run time.
``excludes``
    Patterns to drop from the matched set. Files that Windows holds open (for
    example a 1C planner's ``.1CD`` in monopoly mode) are skipped with a
    ``WARNING`` and the rest of the run continues.
``delete_after_pickup``
    Delete the originals after they are safely copied into staging — useful to
    clear a spill directory of database dumps.
``archive_name``
    When set, all matched files are packed into a single ``tar`` named this,
    instead of being uploaded individually.
``rename_with_timestamp``
    Insert a ``YYYYMMDD-HHMMSS`` timestamp into each file name on upload. Pair
    this with a trigger's ``static_local_name`` (below): the on-disk dump keeps a
    fixed name (only one fresh copy, no accumulation) while the uploaded history
    stays complete.

.. _static_local_name:

Fixed on-disk names (``static_local_name``)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Every database trigger accepts ``static_local_name: true``. When set, the dump
is written to a *fixed* filename (``<db>-full.bak``) instead of a timestamped
one, so each run **overwrites** the previous dump and the staging directory
never accumulates. Combine with ``rename_with_timestamp`` on the source to keep
full history in the cloud:

.. list-table::
   :header-rows: 1
   :widths: 20 80

   * - Trigger
     - File name with ``static_local_name: true``
   * - ``mssql``
     - ``<db>-full.bak`` / ``<db>-diff.bak`` / ``<db>-log.trn``
   * - ``postgres``
     - ``<db>-full.dump`` (or ``.sql`` / ``.tar`` per ``format``)
   * - ``mysql``
     - ``<db>-full.sql`` (or ``all-databases-full.sql``)
   * - ``mongodb``
     - ``<db>-full.tar`` (or ``alldbs-full.tar``)
   * - ``redis``
     - ``redis-full.rdb``
   * - ``sqlite``
     - ``<dbname>-full.sqlite``

.. note::

   When the archive being collected is already a finished ``.tar``/``.tar.zst``
   (weekly/monthly *promote* models), the file is passed through as-is — no
   re-compression. See :doc:`guides/promote`.

Triggers
--------

All database triggers share the idea of an **output directory**; the ``files``
source then picks the dumps up from there.

MSSQL
~~~~~

Connects to SQL Server over TCP and runs ``BACKUP DATABASE`` for each listed
database. The ``.bak`` files land in ``output_dir_windows`` on the SQL host.

.. code-block:: yaml

    triggers:
      - type: mssql
        host: 10.0.0.5
        port: 1433
        username: backup_user
        password: SECRET
        output_dir_windows: "D:\\archive_sql"
        databases:
          - { name: AppDB,   backup_type: FULL }
          - { name: OtherDB, backup_type: DIFFERENTIAL }
        compression: true              # WITH COMPRESSION (unsupported on Express)
        checksum: true                 # WITH CHECKSUM
        copy_only: false               # WITH COPY_ONLY
        timeout: 7200
        encrypt_connection: true       # Encrypt=yes
        trust_server_certificate: true # accept the server cert (legacy servers)

.. warning::

   SQL Server **Express** does not support ``WITH COMPRESSION`` — set
   ``compression: false`` for Express instances or the backup fails with
   error 1844. See :doc:`guides/mssql` for permissions and TLS notes.

PostgreSQL
~~~~~~~~~~

.. code-block:: yaml

    triggers:
      - type: postgres
        host: 127.0.0.1
        port: 5432
        username: postgres
        password: SECRET
        output_dir: /var/spool/backuppy/pg
        databases: [appdb, otherdb]    # empty = all non-template databases
        format: custom                 # custom | plain | tar (pg_dump -F)
        static_local_name: false
        pg_dump_path: pg_dump          # override if multiple client versions
        pg_dumpall_path: pg_dumpall

.. warning::

   ``pg_dump`` must be **the same version or newer** than the PostgreSQL server,
   or it aborts with *"server version mismatch"*. On Debian 12 (client 15)
   against a 16/17 server, install a newer client from the PGDG apt repo and
   point ``pg_dump_path`` / ``pg_dumpall_path`` at, e.g.,
   ``/usr/lib/postgresql/17/bin/pg_dump``.

MySQL / MariaDB
~~~~~~~~~~~~~~~

.. code-block:: yaml

    triggers:
      - type: mysql
        host: 127.0.0.1
        port: 3306
        username: root
        password: SECRET
        output_dir: /var/spool/backuppy/mysql
        databases: [appdb]             # empty = all databases
        single_transaction: true       # consistent dump of InnoDB without locking
        static_local_name: false

MongoDB
~~~~~~~

.. code-block:: yaml

    triggers:
      - type: mongodb
        uri: "mongodb://127.0.0.1:27017"
        output_dir: /var/spool/backuppy/mongo
        databases: [appdb]             # empty = all; one tar per database
        gzip: true                     # mongodump --gzip
        oplog: false                   # mongodump --oplog (point-in-time)
        static_local_name: false

Redis
~~~~~

.. code-block:: yaml

    triggers:
      - type: redis
        host: 127.0.0.1
        port: 6379
        password: ""
        rdb_path: /var/lib/redis/dump.rdb   # where BGSAVE writes the RDB
        output_dir: /var/spool/backuppy/redis
        static_local_name: false

SQLite
~~~~~~

.. code-block:: yaml

    triggers:
      - type: sqlite
        paths: [/srv/app/app.db]
        output_dir: /var/spool/backuppy/sqlite

Uses SQLite's online backup API, so the database can stay in use during the dump.

hook (generic command)
~~~~~~~~~~~~~~~~~~~~~~~~

Run any command that produces files; you are trusted to write them where the
source will find them.

.. code-block:: yaml

    triggers:
      - type: hook
        command: ["/usr/local/bin/dump-something", "--out", "/tmp/staging"]
        output_dir: /tmp/staging     # informational; not enforced
