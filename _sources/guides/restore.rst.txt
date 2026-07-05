Restoring a backup
==================

backuppy stores **plain files** — there is no proprietary catalog or container
format. Restoring is just: fetch the artifact, undo any compression/encryption,
then use the native tool for that data type.

1. Fetch the artifact
---------------------

List what exists in each destination and where::

    backuppy list mymodel

Then download it with whatever fits the destination — the provider's web UI,
``aws s3 cp`` / ``rclone`` for object stores, a WebDAV client for Nextcloud,
or ``scp`` for SFTP.

2. Decompress / decrypt
-----------------------

Reverse whatever the model applied, in reverse order (split → decrypt →
decompress):

- **split:** concatenate the parts (``cat file.tar.zst.part-* > file.tar.zst``).
- **encryption:** ``gpg --decrypt`` (gpg methods) or ``openssl enc -d`` (openssl
  method) with the same passphrase/key.
- **compression:** ``unzstd`` / ``gunzip`` / ``unxz`` / ``bunzip2`` to match the
  ``compression.method``.

3. Restore the data
-------------------

**Files** — extract the tar::

    tar -xf mymodel-data-20260608-104500.tar -C /restore/target

**MSSQL** — in SSMS or ``sqlcmd``::

    RESTORE DATABASE [AppDB] FROM DISK = N'C:\path\to\AppDB-full.bak' WITH REPLACE

For point-in-time recovery, restore the chain FULL → DIFFERENTIAL → LOG using
``WITH NORECOVERY`` for every step except the last, which uses ``WITH RECOVERY``.

**PostgreSQL** — for the default ``custom`` format::

    pg_restore -d target_db mymodel-full.dump

(for ``plain`` format, ``psql -d target_db -f dump.sql``).

**MySQL / MariaDB**::

    mysql target_db < db-full.sql

**MongoDB** — extract the tar, then ``mongorestore`` the dump directory.

**Redis** — stop redis, put the ``.rdb`` in place (``dir`` / ``dbfilename`` from
``redis.conf``), start redis.

**SQLite** — the ``.sqlite`` file *is* the database; copy it into place.

.. tip::

   Test restores periodically against a scratch target. A backup you have never
   restored is a hypothesis, not a backup.
