Backing up SQL Server
=====================

A SQL Server model has two parts: an **mssql trigger** that runs
``BACKUP DATABASE`` into a folder on the Windows host, and a **files source**
that picks the resulting ``.bak`` files up (usually over an SMB share mounted on
the agent).

.. code-block:: yaml

    name: appdb

    triggers:
      - type: mssql
        host: 10.10.111.100
        port: 1433
        username: sa
        password: SECRET
        output_dir_windows: "D:\\archive_sql"
        databases:
          - { name: AppDB, backup_type: FULL }
        compression: false            # Express does NOT support WITH COMPRESSION
        encrypt_connection: true
        trust_server_certificate: true

    sources:
      - type: files
        paths:
          - /mnt/appdb-sql/*.bak      # the SMB mount of D:\archive_sql

    compression: { method: zstd }
    s3: { enabled: true, bucket: my-bucket, keep_last: 30 }

Permissions
-----------

The SQL Server **service account** — not the share user — is what actually
writes the ``.bak`` file, so it needs *Modify* on ``output_dir_windows``. When
agents are managed by the portal this grant is applied automatically at deploy
time (the portal detects the SQL service by its ``sqlservr.exe`` binary and
grants the service SID, which covers virtual accounts such as
``NT SERVICE\MSSQL$INSTANCE``). Granting the *share* user alone is **not**
enough and yields ``BACKUP ... error 3201 / OS error 5 (Access denied)``.

TLS with legacy servers
-----------------------

Modern ODBC drivers require TLS for the login packet even with
``Encrypt=no``. Old SQL Server builds present certificates signed with legacy
algorithms, which the driver rejects (``SSL: sslv3 alert`` / legacy sigalg). Set
``trust_server_certificate: true``; the portal additionally stages an OpenSSL
``SECLEVEL=0`` config per-model when it detects this failure.

Common errors
-------------

.. list-table::
   :header-rows: 1
   :widths: 40 60

   * - Symptom
     - Cause / fix
   * - ``1844 WITH COMPRESSION not supported``
     - Express edition — set ``compression: false``.
   * - ``3201 / Access denied``
     - SQL service account lacks *Modify* on the output dir (see *Permissions*).
   * - ``FilesSource: no files matched``
     - The source glob points at a different folder than ``output_dir_windows``
       — make the mounted share and the output dir agree.
